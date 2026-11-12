"""Dataset maintenance, source snapshots, and non-destructive rebuilds."""
import json
import unittest
from unittest.mock import patch

from backend.datasets import DatasetItem
from backend.store import utc_now
from tests import test_datasets as fixtures


class DatasetLifecycleTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp
    create = fixtures.DatasetApiTests.create
    def upload(self, identifier, text=b'Grounded document', name='source.txt'):
        return self.client.post(f'/api/datasets/{identifier}/documents', files={'files': (name, text)})

    def configure(self, identifier, **overrides):
        options = {'llm': self.selection, 'size': 2, 'num_evolutions': 0, **overrides}
        return self.client.put(f'/api/datasets/{identifier}/generation-config', json=options)

    def build(self, identifier, mode='append'):
        return self.client.post(f'/api/datasets/{identifier}/build?mode={mode}')

    def test_configuration_and_document_import_are_independent_of_generation(self):
        identifier = self.create()
        self.assertEqual(self.configure(identifier, critic_llm=self.selection, prompt='Dataset prompt').status_code, 200)
        response = self.upload(identifier)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['item_count'], 0)
        self.assertEqual(response.json()['source_chunk_count'], 1)
        self.assertEqual(self.upload(identifier).json()['source_chunk_count'], 1)
        self.assertEqual(self.upload(identifier, b'Other document', 'other.md').json()['source_chunk_count'], 2)
        summary = self.client.get(f'/api/datasets/{identifier}/sources').json()
        self.assertEqual(len(summary['items']), 2)
        self.assertEqual({source['kind'] for source in summary['items']}, {'documents'})
        self.assertNotIn('Grounded document', json.dumps(summary))
        before = self.repo.sources(identifier)
        response = self.client.post(f'/api/datasets/{identifier}/documents', files=[
            ('files', ('ok.txt', b'Valid')), ('files', ('broken.exe', b'Invalid'))])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.repo.sources(identifier), before)
        self.assertEqual(self.repo.get(identifier)['generation_config']['prompt'], 'Dataset prompt')

    def test_legacy_sdk_metadata_does_not_prove_replaceable_origin(self):
        legacy = {'input': 'Old or reimported record', 'additional_metadata': {'prismlab_generation': {'sdk': 'deepeval'}}}
        self.assertFalse(self.repo.is_generated(legacy, {'source_type': 'documents'}))
        self.assertFalse(self.repo.is_generated({**legacy, 'origin': 'import'}, {'source_type': 'documents'}))
        self.assertTrue(self.repo.is_generated({**legacy, 'origin': 'generated'}, {'source_type': 'manual'}))

    def test_dify_sync_replaces_only_dify_sources_and_failure_preserves_snapshot(self):
        identifier = self.create()
        self.upload(identifier)
        now = utc_now()
        self.store.put_service_config({'id': 'knowledge', 'name': 'Knowledge', 'provider': 'dify',
                                       'created_at': now, 'updated_at': now, 'models': []})
        def sync(chunks):
            with patch('backend.dataset_routes.fetch_dify_chunks', return_value=chunks):
                return self.client.post(f'/api/datasets/{identifier}/sync-dify', data={'config_id': 'knowledge'})
        self.assertEqual(sync([{'id': 'old', 'content': 'Old knowledge', 'metadata': {'document_name': 'Dify old'}}]).status_code, 200)
        self.assertEqual(sync([{'id': 'new', 'content': 'New knowledge', 'metadata': {'document_name': 'Dify new'}}]).status_code, 200)
        saved = self.repo.sources(identifier)
        self.assertEqual([item['content'] for item in saved], ['Grounded document', 'New knowledge'])
        with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=ValueError('private source')):
            response = self.client.post(f'/api/datasets/{identifier}/sync-dify', data={'config_id': 'knowledge'})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('private source', response.text)
        self.assertEqual(self.repo.sources(identifier), saved)
        def concurrent_change(*args):
            self.repo.mutate(identifier, lambda c, d: d.update(source_revision=d['source_revision'] + 1))
            return []
        with patch('backend.dataset_routes.fetch_dify_chunks', side_effect=concurrent_change):
            self.assertEqual(self.client.post(f'/api/datasets/{identifier}/sync-dify', data={'config_id': 'knowledge'}).status_code, 409)
        self.assertEqual(self.repo.sources(identifier), saved)

    def test_append_build_adds_requested_count_and_uses_saved_models_and_prompt(self):
        identifier = self.create()
        self.repo.add(identifier, [DatasetItem(input='Manual').model_dump()])
        self.upload(identifier)
        self.assertEqual(self.configure(identifier, critic_llm=self.selection, prompt='Saved prompt').status_code, 200)
        def make(chunks, count, config, key, prompt, existing, options, critic, usage):
            self.assertEqual(prompt, 'Saved prompt')
            self.assertEqual(critic[0]['model'], 'test-model')
            self.assertEqual(chunks[0]['content'], 'Grounded document')
            self.assertIn('Manual', existing)
            return [DatasetItem(input=f'Generated {i}').model_dump() for i in range(count)]
        with patch('backend.datasets.generate_items', side_effect=make):
            self.assertEqual(self.build(identifier).status_code, 202)
        dataset = self.repo.get(identifier)
        self.assertEqual(dataset['status'], 'ready')
        self.assertEqual(dataset['item_count'], 3)
        self.assertEqual(dataset['build_progress'], 2)
        self.assertEqual(dataset['generation_options']['size'], 2)
        self.assertEqual(sum(item['origin'] == 'generated' for item in self.repo.items(identifier)['items']), 2)

    def test_rebuild_replaces_only_generated_and_commits_after_complete_generation(self):
        identifier = self.create()
        self.upload(identifier)
        self.configure(identifier, size=6)
        self.repo.add(identifier, [DatasetItem(input='Manual').model_dump()])
        self.repo.add(identifier, [DatasetItem(input='Imported', additional_metadata={'prismlab_generation': {'sdk': 'deepeval'}}).model_dump()], origin='import')
        self.repo.add(identifier, [DatasetItem(input='Old AI').model_dump(), DatasetItem(input='Edited AI').model_dump()], generating=True)
        edited = next(item for item in self.repo.items(identifier)['items'] if item['input'] == 'Edited AI')
        self.repo.update_item(identifier, edited['id'], DatasetItem(input='Edited by human').model_dump())
        initial = {item['id'] for item in self.repo.items(identifier)['items']}
        serial = 0
        def make(chunks, count, *args):
            nonlocal serial
            self.assertEqual({item['id'] for item in self.repo.items(identifier)['items']}, initial)
            values = [DatasetItem(input=f'New AI {serial + i}').model_dump() for i in range(count)]
            serial += count
            return values
        with patch('backend.datasets.generate_items', side_effect=make):
            self.assertEqual(self.build(identifier, 'replace_generated').status_code, 202)
        rows = self.repo.items(identifier, size=100)['items']
        self.assertEqual(len(rows), 9)
        self.assertEqual({item['input'] for item in rows if item['origin'] != 'generated'}, {'Manual', 'Imported', 'Edited by human'})
        self.assertNotIn('Old AI', {item['input'] for item in rows})
        self.assertEqual(self.repo.get(identifier)['status'], 'ready')
        exported = self.client.get(f'/api/datasets/{identifier}/export').json()
        self.assertTrue(all('origin' not in item for item in exported))

    def test_failed_rebuild_preserves_original_data_and_retry_restarts_whole_batch(self):
        identifier = self.create()
        self.upload(identifier)
        self.configure(identifier)
        self.repo.add(identifier, [DatasetItem(input='Original AI').model_dump()], generating=True)
        before = self.repo.items(identifier)['items']
        with patch('backend.datasets.generate_items', side_effect=[[DatasetItem(input='Incomplete').model_dump()]] + [[]] * 20):
            self.assertEqual(self.build(identifier, 'replace_generated').status_code, 202)
        self.assertEqual(self.repo.get(identifier)['status'], 'failed')
        self.assertEqual(self.repo.items(identifier)['items'], before)
        with patch('backend.datasets.generate_items', side_effect=lambda chunks, count, *args: [DatasetItem(input=f'Retried {i}').model_dump() for i in range(count)]):
            self.assertEqual(self.client.post(f'/api/datasets/{identifier}/retry').status_code, 202)
        self.assertEqual(self.repo.get(identifier)['status'], 'ready')
        self.assertEqual(self.repo.get(identifier)['item_count'], 2)

    def test_build_lock_missing_source_and_workspace_scoping(self):
        identifier = self.create()
        self.configure(identifier)
        self.assertEqual(self.build(identifier).status_code, 400)
        self.upload(identifier)
        with patch('backend.dataset_routes.build_dataset'):
            self.assertEqual(self.build(identifier).status_code, 202)
        self.assertEqual(self.build(identifier).status_code, 409)
        self.assertEqual(self.configure(identifier).status_code, 409)
        self.assertEqual(self.upload(identifier).status_code, 409)
        self.assertEqual(self.client.post(f'/api/datasets/{identifier}/items', json={'input': 'Locked'}).status_code, 409)
        self.assertEqual(self.client.get('/api/datasets/missing/sources').status_code, 404)
        self.assertEqual(self.configure('missing').status_code, 404)
        self.assertEqual(self.upload('missing').status_code, 404)
        self.assertEqual(self.build('missing').status_code, 404)

    def test_expand_self_and_scratch_from_existing_dataset(self):
        identifier = self.create()
        self.repo.add(identifier, [DatasetItem(input='Seed', context=['Grounded context']).model_dump()])
        self.configure(identifier, method='goldens', seed_dataset_id=identifier)
        with patch('backend.datasets.generate_items', side_effect=lambda chunks, count, *args: [DatasetItem(input=f'Expanded {i}').model_dump() for i in range(count)]):
            self.assertEqual(self.build(identifier).status_code, 202)
        self.assertEqual(self.repo.get(identifier)['item_count'], 3)
        self.assertEqual(self.repo.sources(identifier)[0]['golden']['input'], 'Seed')
        self.configure(identifier, method='scratch', task='New product questions')
        with patch('backend.datasets.generate_items', side_effect=lambda chunks, count, *args: [DatasetItem(input=f'Scratch {i}').model_dump() for i in range(count)]):
            self.assertEqual(self.build(identifier).status_code, 202)
        self.assertEqual(self.repo.get(identifier)['item_count'], 5)
