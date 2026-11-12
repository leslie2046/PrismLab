"""Workspace review instructions, build snapshots, and actual SDK review calls."""
import json
import unittest
from unittest.mock import patch

from backend.datasets import DEFAULT_CRITIC_PROMPT, DEFAULT_PROMPT, DatasetItem, GenerationOptions, generate_items
from tests import test_datasets as fixtures
from tests.test_deepeval_datasets import SDKFixture


class ReviewPromptApiTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp
    create = fixtures.DatasetApiTests.create

    def test_save_reload_reset_and_old_client_preservation(self):
        initial = self.client.get('/api/dataset-settings').json()
        self.assertEqual(initial['critic_prompt'], DEFAULT_CRITIC_PROMPT)
        self.assertEqual(initial['default_critic_prompt'], DEFAULT_CRITIC_PROMPT)
        response = self.client.put('/api/dataset-settings', json={'critic_prompt': 'Require a specific intent.'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/dataset-settings').json()['critic_prompt'], 'Require a specific intent.')
        response = self.client.put('/api/dataset-settings', json={'prompt': 'Old client generation instructions'})
        self.assertEqual(response.json()['critic_prompt'], 'Require a specific intent.')
        for invalid in ('', ' \n ', 'x' * 20001):
            self.assertEqual(self.client.put('/api/dataset-settings', json={'critic_prompt': invalid}).status_code, 422)
        self.assertEqual(self.client.get('/api/dataset-settings').json()['critic_prompt'], 'Require a specific intent.')
        reset = self.client.put('/api/dataset-settings', json={'critic_prompt': None})
        self.assertEqual(reset.json()['critic_prompt'], DEFAULT_CRITIC_PROMPT)

    def test_new_generation_and_retry_use_original_snapshot(self):
        self.store.save_system_settings({'dataset_default_llm': self.selection, 'dataset_critic_prompt': 'Original review'})
        with patch('backend.datasets.generate_items', side_effect=ValueError('fixture failure')) as generate:
            response = self.client.post('/api/datasets/generate', data={
                'name': 'Review snapshot', 'options': json.dumps({'size': 1})},
                files={'files': ('source.txt', b'Grounded context')})
        self.assertEqual(response.status_code, 202)
        identifier = response.json()['id']
        self.assertEqual(self.repo.get(identifier)['status'], 'failed')
        self.assertEqual(self.repo.get(identifier)['critic_prompt'], 'Original review')
        self.assertEqual(generate.call_args.args[6]['critic_prompt'], 'Original review')
        self.store.save_system_settings({'dataset_critic_prompt': 'Changed review'})
        with patch('backend.datasets.generate_items', return_value=[DatasetItem(input='Retried question').model_dump()]) as generate:
            retry = self.client.post(f'/api/datasets/{identifier}/retry')
        self.assertEqual(retry.status_code, 202)
        self.assertEqual(self.repo.get(identifier)['status'], 'ready')
        self.assertEqual(generate.call_args.args[6]['critic_prompt'], 'Original review')

    def test_each_append_or_rebuild_snapshots_latest_workspace_prompt(self):
        identifier = self.create()
        self.assertEqual(self.client.post(f'/api/datasets/{identifier}/documents',
                         files={'files': ('source.txt', b'Grounded context')}).status_code, 200)
        self.assertEqual(self.client.put(f'/api/datasets/{identifier}/generation-config',
                         json={'llm': self.selection, 'size': 1}).status_code, 200)
        for mode in ('append', 'replace_generated'):
            expected = mode + ' review instructions'
            self.store.save_system_settings({'dataset_critic_prompt': expected})
            with patch('backend.datasets.generate_items', return_value=[DatasetItem(input=mode + ' question').model_dump()]) as generate:
                response = self.client.post(f'/api/datasets/{identifier}/build?mode={mode}')
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.repo.get(identifier)['status'], 'ready')
            self.assertEqual(self.repo.get(identifier)['critic_prompt'], expected)
            self.assertEqual(generate.call_args.args[6]['critic_prompt'], expected)
            # The snapshot must not break strict generation config validation on the next build.
            self.assertNotIn('critic_prompt', self.repo.get(identifier)['generation_options'])


class ReviewPromptSDKTests(unittest.TestCase):
    def test_custom_instructions_only_reach_actual_input_feedback_calls(self):
        config = {'base_url': 'https://fixture.test', 'model': 'generator'}
        chunks = [{'id': 'context', 'content': 'Grounded context', 'metadata': {}}]
        custom = 'Review-marker: require an explicit, answerable intent.'
        for critic in (None, ({**config, 'model': 'reviewer'}, 'reviewer-key')):
            with self.subTest(critic=critic):
                fixture = SDKFixture(score=0.1)
                options = GenerationOptions(size=1, max_quality_retries=2).model_dump()
                with patch('backend.deepeval_generation.stream_chat', side_effect=fixture):
                    items = generate_items(chunks, 1, config, 'key', DEFAULT_PROMPT, [],
                                           {**options, 'critic_prompt': custom}, critic)
                self.assertEqual(len(items), 1)
                reviews = 0
                for model, prompt in fixture.calls:
                    schema = json.loads(prompt.split('No Markdown.\n')[-1])
                    reviewing = schema['title'] == 'InputFeedback'
                    self.assertEqual(custom in prompt, reviewing)
                    if reviewing:
                        reviews += 1
                        self.assertEqual(model, 'reviewer' if critic else 'generator')
                self.assertGreater(reviews, 0)
