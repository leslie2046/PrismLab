"""Multiple knowledge sources, atomic imports, and legacy compatibility."""
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.datasets import DatasetItem, GenerationOptions
from backend.store import utc_now
from tests import test_datasets as fixtures


class DatasetMultiSourceTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp
    create = fixtures.DatasetApiTests.create

    def knowledge(self, identifier):
        now = utc_now()
        self.store.put_service_config({'id': identifier, 'name': f'Knowledge {identifier}', 'provider': 'dify',
            'dataset_id': f'real-{identifier}', 'created_at': now, 'updated_at': now, 'models': []})

    def chunk(self, config, key):
        return [{'id': 'kb-1', 'content': f'Private content {config["id"]}',
                 'metadata': {'segment_id': f'segment-{config["id"]}', 'document_name': 'Same document'}}]

    def generate(self, ids, **extra):
        return self.client.post('/api/datasets/generate', data={'name': 'Multiple knowledge',
            'evaluation_purpose': 'retrieval', 'options': json.dumps({'llm': self.selection,
                'size': 2, 'num_evolutions': 0, 'dify_config_ids': ids, **extra})})

    def sync(self, identifier, ids):
        return self.client.post(f'/api/datasets/{identifier}/sync-dify', data={'config_ids': ids})

    def test_multiple_initial_sources_preserve_identity_and_retrieval_annotations(self):
        for identifier in ['a', 'b']:
            self.knowledge(identifier)
        def make(chunks, count, *args):
            self.assertEqual({c['metadata']['dify_config_id'] for c in chunks}, {'a', 'b'})
            return [DatasetItem(input=f'Question {i}', additional_metadata={
                'prismlab_generation': {'source_ids': [chunk['id']]}}).model_dump() for i,chunk in enumerate(chunks)]
        with patch('backend.datasets.fetch_dify_chunks', side_effect=self.chunk) as fetch, patch('backend.datasets.generate_items', side_effect=make):
            response = self.generate(['a', 'b', 'a'])
        self.assertEqual(response.status_code, 202, response.text)
        identifier = response.json()['id']
        dataset = self.repo.get(identifier)
        self.assertEqual(dataset['status'], 'ready')
        self.assertEqual(dataset['dify_config_ids'], ['a', 'b'])
        self.assertEqual(fetch.call_count, 2)
        chunks = self.repo.sources(identifier)
        self.assertEqual(len({chunk['id'] for chunk in chunks}), 2)
        self.assertEqual({c['metadata']['dataset_id'] for c in chunks}, {'real-a', 'real-b'})
        self.assertEqual({c['metadata']['segment_id'] for c in chunks}, {'segment-a', 'segment-b'})
        summary = self.client.get('/api/datasets').json()['items'][0]['data_sources']
        self.assertEqual({source['name'] for source in summary}, {'Knowledge a', 'Knowledge b'})
        self.assertNotIn('Private content', json.dumps(summary))

    def test_append_then_refresh_preserves_other_knowledge_documents_and_samples(self):
        identifier = self.create()
        for source in ['a', 'b', 'c']:
            self.knowledge(source)
        self.repo.add(identifier, [DatasetItem(input='Retained sample').model_dump()])
        self.client.post(f'/api/datasets/{identifier}/documents', files={'files': ('source.txt', b'Uploaded text')})
        with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=self.chunk):
            self.assertEqual(self.sync(identifier, ['a', 'b']).status_code, 200)
            self.assertEqual(self.sync(identifier, ['c']).status_code, 200)
            self.assertEqual(self.sync(identifier, ['b']).status_code, 200)
        chunks = self.repo.sources(identifier)
        self.assertEqual(len(chunks), 4)
        self.assertEqual({c['metadata'].get('dify_config_id') for c in chunks}, {None, 'a', 'b', 'c'})
        self.assertEqual(self.repo.get(identifier)['dify_config_ids'], ['a', 'b', 'c'])
        self.assertEqual(self.repo.items(identifier)['items'][0]['input'], 'Retained sample')
        self.assertEqual(self.repo.get(identifier)['item_count'], 1)
        self.assertEqual(len(self.client.get(f'/api/datasets/{identifier}/sources').json()['items']), 4)

    def test_partial_fetch_failure_and_combined_limit_are_atomic(self):
        identifier = self.create()
        for source in ['a', 'b']:
            self.knowledge(source)
        self.repo.save_sources(identifier, [{'id': 'existing', 'content': 'Original', 'metadata': {'filename': 'old.txt'}}])
        before = self.repo.get(identifier)
        def fail(config, key):
            if config['id'] == 'b':
                raise ValueError('private connection information')
            return self.chunk(config, key)
        for fetch in [fail, lambda *args: [{'id':'large','content':'x'*1_100_000,'metadata':{}}]]:
            with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=fetch):
                response = self.sync(identifier, ['a', 'b'])
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('private connection', response.text)
            self.assertEqual(self.repo.get(identifier), before)
            self.assertEqual(self.repo.sources(identifier)[0]['content'], 'Original')

    def test_initial_failure_retries_all_sources_and_preserves_no_partial_snapshot(self):
        for source in ['a', 'b']:
            self.knowledge(source)
        def fail(config, key):
            if config['id'] == 'b':
                raise ValueError('Not available')
            return self.chunk(config, key)
        with patch('backend.datasets.fetch_dify_chunks', side_effect=fail):
            response = self.generate(['a','b'])
        identifier = response.json()['id']
        self.assertEqual(self.repo.get(identifier)['status'], 'failed')
        self.assertEqual(self.repo.sources(identifier), [])
        with patch('backend.datasets.fetch_dify_chunks', side_effect=self.chunk) as fetch, patch('backend.datasets.generate_items',
                return_value=[DatasetItem(input='One').model_dump(), DatasetItem(input='Two').model_dump()]):
            response = self.client.post(f'/api/datasets/{identifier}/retry')
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.repo.get(identifier)['status'], 'ready')
        self.assertEqual(len(self.repo.sources(identifier)), 2)

    def test_existing_retrieval_dataset_build_uses_all_imported_snapshots(self):
        for source in ['a', 'b']:
            self.knowledge(source)
        with patch('backend.datasets.fetch_dify_chunks', side_effect=self.chunk), patch('backend.datasets.generate_items',
                return_value=[DatasetItem(input='Original one').model_dump(), DatasetItem(input='Original two').model_dump()]):
            identifier = self.generate(['a']).json()['id']
        with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=self.chunk):
            response = self.sync(identifier, ['b'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repo.get(identifier)['item_count'], 2)
        def make(chunks, count, *args):
            self.assertEqual({c['metadata']['dify_config_id'] for c in chunks}, {'a','b'})
            return [DatasetItem(input='Appended one').model_dump(), DatasetItem(input='Appended two').model_dump()]
        with patch('backend.datasets.fetch_dify_chunks') as fetch, patch('backend.datasets.generate_items', side_effect=make):
            response = self.client.post(f'/api/datasets/{identifier}/build?mode=append')
        self.assertEqual(response.status_code, 202, response.text)
        fetch.assert_not_called()
        self.assertEqual(self.repo.get(identifier)['status'], 'ready')
        self.assertEqual(self.repo.get(identifier)['item_count'], 4)
        self.assertEqual(len(self.repo.sources(identifier)), 2)

    def test_invalid_or_inaccessible_sources_do_not_create_or_update(self):
        self.knowledge('a')
        with patch('backend.datasets.fetch_dify_chunks') as fetch:
            response = self.generate(['a','outside-workspace'])
        self.assertEqual(response.status_code, 400)
        fetch.assert_not_called()
        self.assertEqual(self.repo.page(1,10,'')['total'], 0)
        identifier = self.create()
        with patch('backend.dataset_routes.fetch_dify_chunks') as fetch:
            self.assertEqual(self.sync(identifier, ['a','outside-workspace']).status_code, 400)
        fetch.assert_not_called()
        for ids in [[], [' '], ['a'*65], ['a']*101]:
            self.assertIn(self.sync(identifier, ids).status_code, [400, 422])

    def test_legacy_scalar_and_multiple_source_validation(self):
        opts = GenerationOptions(dify_config_id='legacy')
        self.assertEqual(opts.dify_config_ids, ['legacy'])
        self.assertEqual(GenerationOptions(dify_config_ids=['b','a','b']).dify_config_id, 'b')
        for ids in [[' '], ['a'*65], ['a']*101]:
            with self.assertRaises(ValidationError):
                GenerationOptions(dify_config_ids=ids)
        self.knowledge('legacy')
        identifier = self.create()
        with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=self.chunk):
            response = self.client.post(f'/api/datasets/{identifier}/sync-dify', data={'config_id':'legacy'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['dify_config_ids'], ['legacy'])
        self.assertEqual(self.client.get('/api/datasets').json()['items'][0]['data_sources'][0]['name'], 'Knowledge legacy')
