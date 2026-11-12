"""Purpose defaults apply to omitted values while explicit choices stay stable."""
import json
import unittest
from unittest.mock import patch
from tests import test_datasets as fixtures


class DatasetAnswerDefaultsTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp

    def test_generate_defaults_and_explicit_overrides(self):
        for purpose in ('retrieval','rag','both'):
            for explicit in (None,True,False):
                options={'size':1,'generation_backend':'ragas','llm':self.selection}
                if explicit is not None:options['include_expected_output']=explicit
                with patch('backend.dataset_routes.build_dataset') as build:
                    response=self.client.post('/api/datasets/generate',data={'name':'Purpose default','evaluation_purpose':purpose,'options':json.dumps(options)},files={'files':('source.txt',b'Refund rules')})
                self.assertEqual(response.status_code,202,response.text)
                expected=purpose!='retrieval' if explicit is None else explicit
                self.assertEqual(response.json()['generation_options']['include_expected_output'],expected)
                self.assertEqual(build.call_args.args[7]['include_expected_output'],expected)

    def test_configure_and_build_preserve_explicit_choice(self):
        for purpose in ('retrieval','rag','both'):
            dataset=self.repo.create({'name':'Purpose settings','evaluation_purpose':purpose,'source_type':'manual'})
            identifier=dataset['id']
            self.repo.save_sources(identifier,[{'id':'source','content':'Refund rules','metadata':{}}])
            config={'generation_backend':'ragas','llm':self.selection,'size':1}
            response=self.client.put(f'/api/datasets/{identifier}/generation-config',json=config)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['generation_config']['include_expected_output'],purpose!='retrieval')
            explicit=purpose=='retrieval'
            response=self.client.put(f'/api/datasets/{identifier}/generation-config',json={**config,'include_expected_output':explicit})
            self.assertEqual(response.status_code,200,response.text)
            with patch('backend.dataset_routes.build_dataset'):
                response=self.client.post(f'/api/datasets/{identifier}/build')
            self.assertEqual(response.status_code,202,response.text)
            self.assertEqual(response.json()['generation_options']['include_expected_output'],explicit)

    def test_build_without_saved_config_uses_current_purpose(self):
        self.store.save_system_settings({'dataset_default_llm':self.selection})
        for purpose in ('retrieval','rag','both'):
            dataset=self.repo.create({'name':'Unconfigured','evaluation_purpose':purpose,'source_type':'manual'})
            self.repo.save_sources(dataset['id'],[{'id':'source','content':'Rules','metadata':{}}])
            with patch('backend.dataset_routes.build_dataset'):
                response=self.client.post(f"/api/datasets/{dataset['id']}/build")
            self.assertEqual(response.status_code,202,response.text)
            self.assertEqual(response.json()['generation_options']['include_expected_output'],purpose!='retrieval')
