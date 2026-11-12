"""Embedding defaults are scoped settings; actual builds freeze the selection."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from tests import test_datasets as fixtures


class DatasetEmbeddingDefaultsTests(unittest.TestCase):
    def setUp(self):
        fixtures.DatasetApiTests.setUp(self)
        self.embedding = {'id': 'embedding-default', 'model': 'embed-v1', 'credential_id': 'credential'}
        self.override = {'id': 'embedding-override', 'model': 'embed-v2', 'credential_id': 'credential'}

        def resolve(request):
            if request.benchmark != 'embedding':
                return self.config, 'generator-secret'
            if request.provider_id == 'unavailable':
                raise HTTPException(400, 'Embedding model is unavailable')
            return {'id': request.provider_id, 'model': request.model, 'benchmark': 'embedding',
                    'credential_id': request.credential_id, 'provider': 'vllm', 'base_url': 'https://fixture/embeddings',
                    'models': [{'name': request.model, 'benchmark': 'embedding', 'credentials': [{'id': 'credential'}]}]}, 'embedding-secret'

        self.resolve.side_effect = resolve
        self.store.save_system_settings({'dataset_default_llm': self.selection, 'dataset_default_embedding_model': self.embedding})

    def generate(self, **options):
        with patch('backend.dataset_routes.build_dataset') as build:
            response = self.client.post('/api/datasets/generate', data={
                'name': 'Embedding inheritance', 'evaluation_purpose': 'rag',
                'options': json.dumps({'size': 1, 'generation_backend': 'ragas_native', **options}),
            }, files={'files': ('source.txt', b'Grounded source')})
        return response, build

    def test_settings_validate_embedding_type_merge_and_clear(self):
        with patch('backend.ragas_native.requests.post') as post:
            response = self.client.put('/api/dataset-settings', json={'default_embedding_model': self.override})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['default_embedding_model'], self.override)
            self.assertEqual(response.json()['default_llm'], self.selection)
            self.assertEqual(self.resolve.call_args.args[0].benchmark, 'embedding')
            self.assertEqual(self.resolve.call_args.args[0].credential_id, 'credential')
            post.assert_not_called()
        self.client.put('/api/dataset-settings', json={'prompt': 'Retain embedding'})
        self.assertEqual(self.client.get('/api/dataset-settings').json()['default_embedding_model'], self.override)
        response = self.client.put('/api/dataset-settings', json={'default_embedding_model': None})
        self.assertIsNone(response.json()['default_embedding_model'])

    def test_unavailable_default_is_rejected_without_overwriting_saved_value(self):
        response = self.client.put('/api/dataset-settings', json={
            'default_embedding_model': {**self.override, 'id': 'unavailable'}, 'prompt': 'Must not persist',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/dataset-settings').json()['default_embedding_model'], self.embedding)
        self.assertNotEqual(self.client.get('/api/dataset-settings').json()['prompt'], 'Must not persist')

    def test_generate_inherits_or_overrides_and_freezes_embedding(self):
        for extra, expected in (({}, self.embedding), ({'embedding_model': None}, self.embedding),
                                ({'embedding_model': self.override}, self.override)):
            with self.subTest(extra=extra):
                response, build = self.generate(**extra)
                self.assertEqual(response.status_code, 202, response.text)
                dataset = response.json()
                self.assertEqual(dataset['generation_options']['embedding_model'], expected)
                self.assertEqual(dataset['generation_models']['embedding']['selection'], expected)
                self.assertEqual(build.call_args.args[7]['embedding_model'], expected)
                self.assertEqual(build.call_args.args[9][0]['id'], expected['id'])
                self.assertNotIn('embedding-secret', response.text)

    def test_no_default_prevents_native_build_but_not_deepeval(self):
        self.store.save_system_settings({'dataset_default_embedding_model': None})
        response, build = self.generate()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('默认嵌入模型', response.json()['detail'])
        build.assert_not_called()
        self.assertEqual(self.repo.page(1, 10, '')['total'], 0)
        response, build = self.generate(generation_backend='deepeval')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertIsNone(build.call_args.args[9])

    def test_saved_inheritance_uses_current_default_only_for_new_builds(self):
        dataset = fixtures.DatasetApiTests.create(self)
        self.repo.save_sources(dataset, [{'id': 'source', 'content': 'Grounded source', 'metadata': {}}])
        path = '/api/datasets/' + dataset
        response = self.client.put(path + '/generation-config', json={'generation_backend': 'ragas_native', 'size': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['generation_config']['embedding_model'])
        self.store.save_system_settings({'dataset_default_embedding_model': self.override})
        with patch('backend.dataset_routes.build_dataset') as build:
            response = self.client.post(path + '/build')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['generation_options']['embedding_model'], self.override)
        self.assertEqual(build.call_args.args[9][0]['id'], self.override['id'])
        self.repo.mutate(dataset, lambda c, d: d.update(status='failed'), generating=True)
        self.store.save_system_settings({'dataset_default_embedding_model': self.embedding})
        with patch('backend.dataset_routes.build_dataset') as retry:
            response = self.client.post(path + '/retry')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(retry.call_args.args[7]['embedding_model'], self.override)
        self.assertEqual(retry.call_args.args[9][0]['id'], self.override['id'])

    def test_saved_override_survives_default_changes(self):
        dataset = fixtures.DatasetApiTests.create(self)
        self.repo.save_sources(dataset, [{'id': 'source', 'content': 'Grounded source', 'metadata': {}}])
        path = '/api/datasets/' + dataset
        response = self.client.put(path + '/generation-config', json={
            'generation_backend': 'ragas_native', 'embedding_model': self.override, 'size': 1,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.store.save_system_settings({'dataset_default_embedding_model': None})
        with patch('backend.dataset_routes.build_dataset'):
            response = self.client.post(path + '/build')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()['generation_options']['embedding_model'], self.override)
