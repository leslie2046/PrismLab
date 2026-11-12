"""Retry budgets and cancellation must preserve data and fence stale workers."""
import threading
import time
import unittest
from unittest.mock import patch

import requests

from backend.dataset_build_control import build_state, BuildControl, BuildRepository, RequestLimitExceeded
from backend.datasets import build_dataset, DatasetItem, DEFAULT_PROMPT
from shared.llm_stream import stream_chat
from shared.request_control import current_control, GenerationStopped
from tests import test_datasets as fixtures


class DatasetBuildControlTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp

    def dataset(self, size=20, replacing=False):
        return self.repo.create(dict(name='Controlled build', status='generating', source_type='documents',
            target_size=size, build_target=size, build_progress=0, build_baseline=0,
            build_mode='replace_generated' if replacing else 'append',
            generation_options={'generation_backend': 'deepeval'}, **build_state()))

    def build(self, dataset):
        build_dataset(self.repo, dataset['id'], [{'id': 'fixture', 'content': 'Fixture', 'metadata': {}}],
            None, self.config, 'secret', DEFAULT_PROMPT, build_token=dataset['build_token'])

    def cancel(self, dataset):
        response = self.client.post(f"/api/datasets/{dataset['id']}/cancel")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_three_failures_stop_large_build_without_exposing_request_details(self):
        dataset = self.dataset(size=500)
        with patch('backend.datasets.generate_items', side_effect=requests.Timeout('secret-url secret-key')) as generate:
            self.build(dataset)
        self.assertEqual(generate.call_count, 3)
        saved = self.repo.get(dataset['id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['build_retry_count'], 2)
        self.assertIn('重试上限', saved['error'])
        self.assertNotIn('secret', saved['error'])

    def test_progress_resets_consecutive_failure_budget(self):
        dataset = self.dataset(size=6)
        one = [DatasetItem(input='One').model_dump()]
        rest = [DatasetItem(input=f'Rest {i}').model_dump() for i in range(5)]
        with patch('backend.datasets.generate_items', side_effect=[requests.Timeout(), requests.Timeout(), one,
                requests.Timeout(), requests.Timeout(), rest]) as generate:
            self.build(dataset)
        self.assertEqual(generate.call_count, 6)
        saved = self.repo.get(dataset['id'])
        self.assertEqual(saved['status'], 'ready')
        self.assertEqual(saved['build_progress'], 6)
        self.assertEqual(saved['build_retry_count'], 0)

    def test_cancel_during_generation_discards_late_result_and_retains_originals(self):
        for replacing in [False, True]:
            with self.subTest(replacing=replacing):
                dataset = self.dataset(replacing=replacing)
                self.repo.add(dataset['id'], [DatasetItem(input='Original').model_dump()], generating=True)
                before = self.repo.items(dataset['id'])['items']
                def generate(*args):
                    self.cancel(dataset)
                    return [DatasetItem(input='Late result').model_dump()]
                with patch('backend.datasets.generate_items', side_effect=generate) as generate_mock:
                    self.build(dataset)
                self.assertEqual(generate_mock.call_count, 1)
                self.assertEqual(self.repo.get(dataset['id'])['status'], 'cancelled')
                self.assertEqual(self.repo.items(dataset['id'])['items'], before)
                self.assertEqual(self.cancel(dataset)['status'], 'cancelled')

    def test_cancel_after_partial_append_keeps_saved_samples(self):
        dataset = self.dataset(size=6)
        def cancel_later(*args):
            self.cancel(dataset)
            raise requests.Timeout()
        calls = 0
        def generate(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [DatasetItem(input='Saved').model_dump()]
            return cancel_later()
        with patch('backend.datasets.generate_items', side_effect=generate):
            self.build(dataset)
        self.assertEqual(self.repo.get(dataset['id'])['build_progress'], 1)
        self.assertEqual([item['input'] for item in self.repo.items(dataset['id'])['items']], ['Saved'])
        self.assertEqual(self.repo.get(dataset['id'])['status'], 'cancelled')

    def test_old_worker_cannot_change_new_build_items_sources_or_usage(self):
        dataset = self.dataset()
        self.cancel(dataset)
        self.repo.mutate(dataset['id'], lambda c, d: d.update(**build_state(), status='generating'))
        worker = BuildRepository(self.repo, dataset['build_token'])
        before = self.repo.get(dataset['id'])
        actions = [lambda: worker.add(dataset['id'], [DatasetItem(input='Stale').model_dump()], generating=True),
            lambda: worker.replace_generated(dataset['id'], []),
            lambda: worker.save_sources(dataset['id'], [{'content': 'Stale'}]),
            lambda: worker.mutate(dataset['id'], lambda c, d: d.update(status='failed'), generating=True)]
        for action in actions:
            with self.assertRaises(GenerationStopped):
                action()
        with patch('backend.datasets.generate_items') as generate:
            self.build(dataset)
        generate.assert_not_called()
        self.assertEqual(self.repo.get(dataset['id']), before)
        self.assertEqual(self.repo.sources(dataset['id']), [])

    def test_live_request_counts_and_cancellation_interrupt_wait(self):
        dataset = self.dataset()
        entered, release = threading.Event(), threading.Event()
        errors = []
        def operation(*args, **kwargs):
            entered.set()
            release.wait(5)
            return ({'output_tokens': 10}, 'fixture')
        control = BuildControl(self.repo, dataset['id'], dataset['build_token'])
        def run():
            scope = current_control.set(control)
            try:
                stream_chat('unused', 'model', None, 'prompt', timeout=3)
            except Exception as error:
                errors.append(error)
            finally:
                current_control.reset(scope)
        try:
            with patch('shared.llm_stream._stream_chat', side_effect=operation):
                thread = threading.Thread(target=run)
                thread.start()
                self.assertTrue(entered.wait(2))
                saved = self.repo.get(dataset['id'])
                self.assertEqual(saved['build_requests'], 1)
                self.assertEqual(saved['build_active_requests'], 1)
                self.cancel(dataset)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertIsInstance(errors[0], GenerationStopped)
                self.assertEqual(self.repo.get(dataset['id'])['build_output_tokens'], 0)
        finally:
            release.set()

    def test_hard_deadline_and_sdk_request_budget(self):
        dataset = self.dataset()
        control = BuildControl(self.repo, dataset['id'], dataset['build_token'])
        for _ in range(3):
            with self.assertRaises(requests.Timeout):
                control.execute(lambda: time.sleep(.2), .02)
        with self.assertRaises(RequestLimitExceeded):
            control.execute(lambda: self.fail('budget must block another request'), 1)
        saved = self.repo.get(dataset['id'])
        self.assertEqual(saved['build_requests'], 3)
        self.assertEqual(saved['build_active_requests'], 0)

    def test_cancel_only_running_and_workspace_scoped(self):
        self.assertEqual(self.client.post('/api/datasets/missing/cancel').status_code, 404)
        ready = self.repo.create({'name': 'Ready'})
        self.assertEqual(self.client.post(f"/api/datasets/{ready['id']}/cancel").status_code, 409)
