import json
import unittest
from unittest.mock import patch

from backend.datasets import GenerationOptions, build_dataset
from backend.ragas_generation import RAGAS_BACKEND_VERSION, generate_items
from tests.test_datasets import DatasetApiTests


class RagasGenerationTests(unittest.TestCase):
    config = {'base_url': 'https://fixture.invalid/v1', 'model': 'fixture-model'}
    chunks = [{'id': 'chunk-1', 'content': '退款期限为七天。', 'metadata': {'filename': 'policy.txt'}}]

    def test_ragas_ignores_deepeval_evolution_rules(self):
        options = {'method': 'scratch', 'task': '退款问题', 'evolutions': {'MULTICONTEXT': 1}}
        self.assertEqual(GenerationOptions(generation_backend='ragas', **options).method, 'scratch')
        with self.assertRaises(ValueError):
            GenerationOptions(generation_backend='deepeval', **options)
        self.assertEqual(GenerationOptions(generation_backend='ragas', evolutions={}).evolutions, {})
        with self.assertRaises(ValueError):
            GenerationOptions(generation_backend='deepeval', evolutions={})

    def test_generation_instructions_and_seed_contexts_reach_the_model(self):
        seeds = [{'golden': {'input': '种子退款问题', 'context': ['七天退款政策']},
                  'metadata': {'seed_item_id': 'seed'}}]
        with patch('backend.ragas_generation.stream_chat', return_value=({'output_tokens': 1},
                   '[{"user_input":"新退款问题","reference":"七天"}]')) as call:
            rows = generate_items(seeds, 1, self.config, 'key', '只提与退款期限有关的问题', [],
                                  {'method': 'goldens', 'task': '不同的申请场景'})
        request = call.call_args.args[3]
        for text in ['只提与退款期限有关的问题', '种子退款问题', '七天退款政策', '不同的申请场景']:
            self.assertIn(text, request)
        self.assertEqual(rows[0]['context'], ['七天退款政策'])
        self.assertEqual(rows[0]['expected_output'], '七天')

    def test_seed_without_context_cannot_invent_reference_answers(self):
        with patch('backend.ragas_generation.stream_chat', return_value=({},
                   '[{"user_input":"新问题","reference":"无依据的答案"}]')):
            rows = generate_items([{'golden': {'input': '种子问题'}, 'metadata': {}}],
                                  1, self.config, 'key', 'task', [], {'method': 'goldens'})
        self.assertIsNone(rows[0]['expected_output'])

    def test_backend_option_defaults_to_deepeval_and_accepts_ragas(self):
        self.assertEqual(GenerationOptions(size=1).generation_backend, 'deepeval')
        self.assertEqual(GenerationOptions(size=1, generation_backend='ragas').generation_backend, 'ragas')
        with self.assertRaises(ValueError):
            GenerationOptions(size=1, generation_backend='unknown')

    def test_ragas_shape_maps_to_local_golden_and_tracks_metadata(self):
        usage = {'requests': 0, 'output_tokens': 0}
        response = {'output_tokens': 17}
        content = json.dumps([{
            'user_input': '退款可以在几天内申请？',
            'reference': '退款申请期限为七天。',
            'reference_contexts': ['退款期限为七天。'],
        }], ensure_ascii=False)
        with patch('backend.ragas_generation.stream_chat', return_value=(response, content)) as call:
            rows = generate_items(self.chunks, 1, self.config, 'fixture-key', '回答政策问题', [],
                                  {'generation_backend': 'ragas', 'language': 'zh-CN'}, usage=usage)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['input'], '退款可以在几天内申请？')
        self.assertEqual(rows[0]['expected_output'], '退款申请期限为七天。')
        self.assertEqual(rows[0]['context'], ['退款期限为七天。'])
        self.assertEqual(rows[0]['additional_metadata']['prismlab_generation']['sdk'], 'ragas')
        self.assertEqual(rows[0]['additional_metadata']['prismlab_generation']['sdk_version'], RAGAS_BACKEND_VERSION)
        self.assertEqual(usage, {'requests': 1, 'output_tokens': 17})
        self.assertEqual(call.call_args.args[2], 'fixture-key')

    def test_ragas_accepts_aliases_deduplicates_and_supports_question_only(self):
        content = '```json\n' + json.dumps({'samples': [
            {'question': '已有问题', 'answer': '重复答案'},
            {'user_input': '新问题', 'reference': None, 'reference_contexts': []},
        ]}, ensure_ascii=False) + '\n```'
        with patch('backend.ragas_generation.stream_chat', return_value=({'output_tokens': 0}, content)):
            rows = generate_items([], 2, self.config, 'key', 'task', ['已有问题'],
                                  {'generation_backend': 'ragas', 'include_expected_output': False})
        self.assertEqual([row['input'] for row in rows], ['新问题'])
        self.assertIsNone(rows[0]['expected_output'])
        self.assertEqual(rows[0]['context'], [])


class RagasRouteTests(DatasetApiTests):
    def test_ragas_creation_save_build_and_retry_do_not_resolve_a_critic(self):
        invalid_critic = {'id': 'removed-critic', 'model': 'removed-model'}
        self.store.save_system_settings({'dataset_default_critic_llm': invalid_critic})
        options = {'generation_backend': 'ragas', 'size': 2, 'llm': self.selection,
                   'critic_llm': invalid_critic, 'evolutions': {}}
        with patch('backend.dataset_routes.build_dataset'):
            result = self.client.post('/api/datasets/generate', data={
                'name': 'RAGAS with stale critic', 'options': json.dumps(options),
            }, files={'files': ('source.txt', b'fixture context')})
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(self.resolve.call_count, 1)
        dataset = result.json()
        self.assertIsNone(dataset['generation_options']['critic_llm'])
        identifier = dataset['id']
        self.repo.mutate(identifier, lambda c, d: d.update(status='failed'), generating=True)
        for action in ['configure', 'build', 'retry']:
            self.resolve.reset_mock()
            with patch('backend.dataset_routes.build_dataset'):
                if action == 'configure':
                    result = self.client.put(f'/api/datasets/{identifier}/generation-config', json=options)
                    self.assertIsNone(result.json()['generation_config']['critic_llm'])
                elif action == 'build':
                    result = self.client.post(f'/api/datasets/{identifier}/build?mode=append')
                else:
                    self.repo.mutate(identifier, lambda c, d: d['generation_options'].update(critic_llm=invalid_critic))
                    result = self.client.post(f'/api/datasets/{identifier}/retry')
            self.assertIn(result.status_code, [200, 202], result.text)
            self.assertEqual(self.resolve.call_count, 1)
            self.repo.mutate(identifier, lambda c, d: d.update(status='failed'), generating=True)

    def test_generate_route_persists_ragas_backend_and_snapshot(self):
        options = {
            'generation_backend': 'ragas', 'size': 2, 'llm': self.selection,
            'method': 'contexts', 'language': 'zh-CN', 'evolutions': {'CONCRETIZING': 1},
        }
        with patch('backend.dataset_routes.build_dataset') as build:
            response = self.client.post('/api/datasets/generate', data={
                'name': 'RAGAS backend dataset', 'options': json.dumps(options),
            }, files={'files': ('source.txt', b'RAGAS source text')})
        self.assertEqual(response.status_code, 202, response.text)
        dataset = response.json()
        self.assertEqual(dataset['sdk'], 'ragas')
        self.assertEqual(dataset['generation_options']['generation_backend'], 'ragas')
        self.assertEqual(dataset['sdk_version'], RAGAS_BACKEND_VERSION)
        self.assertTrue(build.called)

    def test_build_dispatches_to_ragas_adapter(self):
        dataset = self.repo.create({'name': 'Dispatch', 'description': '', 'source_type': 'scratch',
                                    'status': 'generating', 'target_size': 1, 'build_target': 1,
                                    'build_baseline': 0, 'generation_options': {
                                        'generation_backend': 'ragas', 'method': 'scratch', 'task': 'questions',
                                        'size': 1, 'evolutions': {'CONCRETIZING': 1},
                                    }})
        item = {'input': 'RAGAS generated question', 'expected_output': None, 'context': [],
                'additional_metadata': {'prismlab_generation': {'sdk': 'ragas'}}}
        with patch('backend.datasets.generate_ragas_items', return_value=[item]) as ragas, \
             patch('backend.datasets.generate_items') as deepeval:
            build_dataset(self.repo, dataset['id'], [], None, self.config, 'fixture-key', 'prompt',
                          dataset['generation_options'])
        ragas.assert_called_once()
        deepeval.assert_not_called()
        self.assertEqual(self.repo.get(dataset['id'])['status'], 'ready')
        self.assertEqual(self.repo.items(dataset['id'])['items'][0]['input'], 'RAGAS generated question')
