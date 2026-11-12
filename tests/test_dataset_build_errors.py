"""HTTP failures must remain actionable without exposing provider secrets."""
import unittest
from unittest.mock import patch

import requests

from backend.datasets import DEFAULT_PROMPT, DatasetItem, build_dataset
from tests.test_datasets import DatasetApiTests as DatasetFixtures


class DatasetBuildErrorTests(unittest.TestCase):
    setUp = DatasetFixtures.setUp

    def dataset(self, backend='ragas', replacing=False):
        return self.repo.create({
            'name': 'HTTP failure', 'source_type': 'documents', 'status': 'generating',
            'target_size': 2, 'build_target': 2, 'build_baseline': 0,
            'build_mode': 'replace_generated' if replacing else 'append',
            'generation_options': {'generation_backend': backend},
        })

    def http_error(self, status):
        response = requests.Response()
        response.status_code = status
        response._content = b'private-key private-source'
        return requests.HTTPError('private-key https://secret', response=response)

    def build(self, dataset):
        build_dataset(self.repo, dataset['id'], [{'id': 'source', 'content': 'fixture', 'metadata': {}}],
                      None, self.config, 'private-key', DEFAULT_PROMPT)

    def test_permanent_http_errors_stop_after_one_request_for_both_backends(self):
        for backend, target in [('ragas', 'generate_ragas_items'), ('deepeval', 'generate_items')]:
            for status, reason in [(400, '参数'), (401, '认证'), (402, '余额或额度不足'),
                                   (403, '权限'), (404, '不存在'), (422, '参数')]:
                with self.subTest(backend=backend, status=status):
                    dataset = self.dataset(backend)

                    def fail(*args):
                        args[-1]['requests'] += 1
                        raise self.http_error(status)

                    with patch('backend.datasets.' + target, side_effect=fail) as generate:
                        self.build(dataset)
                    generate.assert_called_once()
                    saved = self.repo.get(dataset['id'])
                    self.assertEqual(saved['status'], 'failed')
                    self.assertIn(f'HTTP {status}', saved['error'])
                    self.assertIn(reason, saved['error'])
                    self.assertEqual(saved['generation_usage']['requests'], 1)
                    self.assertNotIn('private', saved['error'])
                    self.assertNotIn('https://', saved['error'])

    def test_http_failure_preserves_original_records_during_rebuild(self):
        dataset = self.dataset(replacing=True)
        self.repo.add(dataset['id'], [DatasetItem(input='Original').model_dump()], generating=True)
        original = self.repo.items(dataset['id'])['items']
        with patch('backend.datasets.generate_ragas_items', side_effect=[
            [DatasetItem(input='Staged').model_dump()], self.http_error(402),
        ]) as generate:
            self.build(dataset)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(self.repo.items(dataset['id'])['items'], original)
        self.assertIn('原有数据已保留', self.repo.get(dataset['id'])['error'])

    def test_transient_http_errors_can_recover(self):
        for status in [408, 429, 500, 503]:
            with self.subTest(status=status):
                dataset = self.dataset()
                with patch('backend.datasets.generate_ragas_items', side_effect=[
                    self.http_error(status),
                    [DatasetItem(input='One').model_dump(), DatasetItem(input='Two').model_dump()],
                ]) as generate:
                    self.build(dataset)
                self.assertEqual(generate.call_count, 2)
                self.assertEqual(self.repo.get(dataset['id'])['status'], 'ready')

    def test_partial_append_survives_failure_and_can_resume(self):
        dataset = self.dataset()
        with patch('backend.datasets.generate_ragas_items', side_effect=[
            [DatasetItem(input='One').model_dump()], self.http_error(402),
        ]):
            self.build(dataset)
        self.assertEqual(self.repo.get(dataset['id'])['item_count'], 1)
        self.repo.mutate(dataset['id'], lambda c, d: d.update(status='generating', error=None))
        with patch('backend.datasets.generate_ragas_items', return_value=[DatasetItem(input='Two').model_dump()]):
            self.build(dataset)
        self.assertEqual(self.repo.get(dataset['id'])['status'], 'ready')
        self.assertEqual(self.repo.get(dataset['id'])['item_count'], 2)
