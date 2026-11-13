import json
import unittest
from unittest.mock import patch
from tests import test_datasets as fixtures

class DatasetConcurrencyDefaultsTests(unittest.TestCase):
    def setUp(self):
        fixtures.DatasetApiTests.setUp(self)
        self.store.save_system_settings({'dataset_default_llm': self.selection, 'dataset_max_concurrent': 7, 'evaluation_max_concurrent': 6})
    def test_settings_validate_and_merge(self):
        response=self.client.get('/api/dataset-settings'); self.assertEqual(response.json()['max_concurrent'],7)
        response=self.client.put('/api/dataset-settings',json={'max_concurrent':5}); self.assertEqual(response.status_code,200); self.assertEqual(response.json()['max_concurrent'],5)
        response=self.client.put('/api/dataset-settings',json={'max_concurrent':11}); self.assertEqual(response.status_code,422); self.assertEqual(self.client.get('/api/dataset-settings').json()['max_concurrent'],5)
        self.assertEqual(self.client.get('/api/dataset-settings').json()['evaluation_max_concurrent'],6)
        response=self.client.put('/api/dataset-settings',json={'evaluation_max_concurrent':4}); self.assertEqual(response.status_code,200); self.assertEqual(response.json()['evaluation_max_concurrent'],4)
        response=self.client.put('/api/dataset-settings',json={'evaluation_max_concurrent':11}); self.assertEqual(response.status_code,422); self.assertEqual(self.client.get('/api/dataset-settings').json()['evaluation_max_concurrent'],4)
    def test_new_generation_uses_workspace_value(self):
        with patch('backend.dataset_routes.build_dataset') as build:
            response=self.client.post('/api/datasets/generate',data={'name':'Concurrency','evaluation_purpose':'rag','options':json.dumps({'size':1,'generation_backend':'deepeval','max_concurrent':1})},files={'files':('source.txt',b'Grounded source')})
        self.assertEqual(response.status_code,202,response.text);self.assertEqual(response.json()['generation_options']['max_concurrent'],7);self.assertEqual(build.call_args.args[7]['max_concurrent'],7)
    def test_saved_dataset_uses_current_workspace_value_and_drops_override(self):
        dataset=self.repo.create({'name':'Concurrency saved','evaluation_purpose':'rag','source_type':'manual'});self.repo.save_sources(dataset['id'],[{'id':'source','content':'Grounded source','metadata':{}}])
        response=self.client.put('/api/datasets/'+dataset['id']+'/generation-config',json={'generation_backend':'deepeval','method':'contexts','max_concurrent':1,'size':1});self.assertEqual(response.status_code,200,response.text);self.assertNotIn('max_concurrent',response.json()['generation_config'])
        with patch('backend.dataset_routes.build_dataset') as build:
            response=self.client.post('/api/datasets/'+dataset['id']+'/build')
        self.assertEqual(response.status_code,202,response.text);self.assertEqual(response.json()['generation_options']['max_concurrent'],7);self.assertEqual(build.call_args.args[7]['max_concurrent'],7)
