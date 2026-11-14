"""Exercise answer regeneration through the API and actual streaming adapter."""
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

import requests

from backend.dataset_trace import DatasetTraceStore
from tests import test_datasets as fixtures


class AnswerRegenerationTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp

    def prepare(self, count=2):
        self.store.save_system_settings({'dataset_default_llm': self.selection})
        self.dataset = self.repo.create({'name': 'Answer regeneration'})
        self.identifier = self.dataset['id']
        self.repo.add(self.identifier, [
            {'input': f'Fixed question {i}', 'expected_output': f'Old answer {i}',
             'context': [f'Fixed context {i}', 'Another source'],
             'additional_metadata': {'source': 'unchanged'}} for i in range(count)])
        self.original = self.repo.items(self.identifier)['items']
        self.ids = [item['id'] for item in self.original]

    def stream(self, text):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_lines.return_value = [
            'data: {"choices":[{"delta":{"content":"' + text + '"},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":10,"completion_tokens":5}}', 'data: [DONE]']
        return response

    def test_answers_are_updated_with_trace_and_questions_contexts_sources_preserved(self):
        self.prepare()
        with patch('backend.datasets.generation_parameters', return_value={'max_tokens':512}), \
             patch('shared.llm_stream.requests.post', side_effect=[self.stream('New answer 0'), self.stream('New answer 1')]) as post:
            response = self.client.post(f'/api/datasets/{self.identifier}/regenerate-answers', json={'ids': self.ids})
        self.assertEqual(response.status_code, 202, response.text)
        state = self.repo.get(self.identifier)
        self.assertEqual(state['status'], 'ready', state)
        self.assertEqual(state['build_progress'], 2)
        self.assertEqual(state['build_requests'], 2)
        self.assertEqual(state['build_answer_pending_ids'], [])
        for index, (old, new) in enumerate(zip(self.original, self.repo.items(self.identifier)['items'])):
            self.assertEqual(new['expected_output'], f'New answer {index}')
            for field in set(old) - {'expected_output', 'generation_trace_id', 'generation_record_id', 'updated_at'}:
                self.assertEqual(new[field], old[field], field)
            details = DatasetTraceStore(self.repo).get(self.identifier, new['id'])
            self.assertEqual(details['availability'], 'recorded')
            self.assertEqual(details['trace']['method'], 'answer_regeneration')
            self.assertEqual(details['sample']['expected_output'], new['expected_output'])
            trace_requests = DatasetTraceStore(self.repo).requests(self.identifier, new['id'], 1, 10)
            self.assertEqual(trace_requests['total'], 1)
            body = post.call_args_list[index].kwargs['json']
            self.assertIn(old['input'], body['messages'][0]['content'])
            self.assertIn(old['context'][0], body['messages'][0]['content'])

    def test_retry_only_pending_answers_after_bounded_model_failure(self):
        self.prepare()
        with patch('backend.datasets.generation_parameters', return_value={'max_tokens':512}), \
             patch('shared.llm_stream.requests.post', side_effect=[self.stream('Kept new answer'), requests.Timeout(), requests.Timeout(), requests.Timeout()]) as post:
            self.client.post(f'/api/datasets/{self.identifier}/regenerate-answers', json={'ids': self.ids})
        state = self.repo.get(self.identifier)
        self.assertEqual(state['status'], 'failed', state)
        self.assertEqual(state['build_progress'], 1)
        self.assertEqual(post.call_count, 4)
        self.assertEqual(state['build_answer_pending_ids'], self.ids[1:])
        partial = deepcopy(self.repo.items(self.identifier)['items'])
        self.assertEqual(partial[0]['expected_output'], 'Kept new answer')
        self.assertEqual(partial[1]['expected_output'], self.original[1]['expected_output'])
        with patch('backend.datasets.generation_parameters', return_value={'max_tokens':512}), \
             patch('shared.llm_stream.requests.post', return_value=self.stream('Resumed answer')) as post:
            response = self.client.post(f'/api/datasets/{self.identifier}/retry')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.repo.get(self.identifier)['status'], 'ready')
        self.assertEqual(self.repo.get(self.identifier)['build_progress'], 2)
        updated = self.repo.items(self.identifier)['items']
        self.assertEqual(updated[0], partial[0])
        self.assertEqual(updated[1]['expected_output'], 'Resumed answer')
