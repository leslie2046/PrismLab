"""Activation is durable, scoped, credential-preserving and enforced beyond the UI."""
import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.model_configs import configured_models
from backend.schemas import ActivationUpdate, ModelActivationUpdate, PlaygroundRequest, ServiceConfigCreate
from backend.secrets import SecretBox
from tests.database import create_test_store


class ModelActivationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = create_test_store(self, Path(directory.name))
        self.box = SecretBox.from_data_dir(Path(directory.name))
        for name, value in [('store', self.store), ('secret_box', self.box)]:
            patcher = patch.object(main, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.config = main.create_service_config(ServiceConfigCreate(name='Provider', provider='vllm', models=[
            {'name':'shared-model','alias':'primary','benchmark':'llm','credentials':[{'name':'One','server_url':'https://fixture.test','api_key':'fixture-only'}]},
            {'name':'shared-model','alias':'standby','benchmark':'llm','credentials':[{'name':'Two','server_url':'https://fixture.test','api_key':'fixture-other'}]},
            {'name':'embedding','benchmark':'embedding','base_url':'https://fixture.test/v1/embeddings'},
        ]))
        self.id = self.config['id']
        app = FastAPI()
        app.router.routes.extend(route for route in main.app.router.routes if getattr(route, 'path', '').startswith(('/api/service-configs', '/api/provider-models')))
        self.client = TestClient(app); self.addCleanup(self.client.close)

    def toggle_model(self, model, enabled):
        return self.client.patch(f'/api/service-configs/{self.id}/models/enabled', json={'model':model,'enabled':enabled})

    def toggle_provider(self, enabled):
        return self.client.patch('/api/provider-models/vllm/enabled', json={'enabled':enabled})

    def llm_catalog(self):
        return next(b for b in main.catalog()['benchmarks'] if b['id']=='llm')

    def test_default_enabled_and_partial_model_disable_filters_catalog_only(self):
        self.assertTrue(self.config['enabled'])
        self.assertTrue(all(model['enabled'] for model in self.config['models']))
        before = self.store.get_service_config(self.id)
        response = self.toggle_model('primary', False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([m['alias'] for m in self.llm_catalog()['providers'][0]['models']], ['standby'])
        self.assertEqual(len(self.client.get('/api/service-configs').json()[0]['models']), 3)
        after = self.store.get_service_config(self.id)
        for previous, current in zip(before['models'], after['models']):
            self.assertEqual(previous['credentials'], current['credentials'])
        self.assertTrue(after['models'][1]['enabled'])
        self.assertNotIn('api_key_encrypted', response.text)
        self.assertNotIn('fixture-only', response.text)
        self.assertEqual(self.toggle_model('primary', True).status_code, 200)
        self.assertEqual(len(self.llm_catalog()['providers'][0]['models']), 2)

    def test_parent_restore_preserves_each_child_and_removes_all_benchmarks(self):
        self.toggle_model('standby', False)
        before = self.store.get_service_config(self.id)
        self.assertEqual(self.toggle_provider(False).status_code, 200)
        self.assertTrue(all(not b['providers'] for b in main.catalog()['benchmarks']))
        self.assertIn('vllm', [p['id'] for p in self.llm_catalog()['provider_kinds']])
        self.assertEqual(self.toggle_provider(True).status_code, 200)
        after = self.store.get_service_config(self.id)
        self.assertEqual(before['models'], after['models'])
        self.assertEqual([m['alias'] for m in self.llm_catalog()['providers'][0]['models']], ['primary'])

    def test_resolution_blocks_disabled_parent_and_model_before_any_external_call(self):
        request = PlaygroundRequest(benchmark='llm', provider_id=self.id, model='primary', query='question')
        self.toggle_model('primary', False)
        with patch('backend.main.run_playground') as call:
            with self.assertRaises(HTTPException) as error: main.playground(request)
            self.assertEqual(error.exception.status_code, 400)
            self.assertIn('已关闭', error.exception.detail)
            call.assert_not_called()
        resolved = main._resolve_configs('llm', [{'id':self.id}])[0]
        self.assertEqual(resolved['credential_id'], self.config['models'][1]['credentials'][0]['id'])
        self.toggle_provider(False)
        with self.assertRaises(HTTPException) as error: main._resolve_configs('embedding', [{'id':self.id,'model':'embedding'}])
        self.assertIn('供应商', error.exception.detail)
        self.assertIn('已关闭', error.exception.detail)

    def test_old_editor_payload_and_rename_cannot_reenable_disabled_settings(self):
        self.toggle_model('primary', False); self.toggle_provider(False)
        payload = copy.deepcopy(self.config)
        payload.pop('enabled')
        for model in payload['models']: model.pop('enabled')
        payload['models'][0]['alias'] = 'renamed'
        updated = main.update_service_config(self.id, ServiceConfigCreate(**payload))
        self.assertFalse(updated['enabled']); self.assertFalse(updated['models'][0]['enabled'])
        credentials = self.store.get_service_config(self.id)['models'][0]['credentials']
        self.assertEqual(self.box.decrypt(credentials[0]['api_key_encrypted']), 'fixture-only')
        self.assertEqual(main.catalog()['benchmarks'][0]['providers'], [])

    def test_legacy_records_are_enabled_by_default_and_keep_endpoints_on_toggle(self):
        legacy = self.store.get_service_config(self.id)
        legacy.pop('enabled'); legacy.update(models=['legacy-a','legacy-b'],benchmark='llm',base_url='https://legacy.test/v1/chat/completions')
        self.store.put_service_config(legacy)
        public = main.list_service_configs()[0]
        self.assertTrue(public['enabled']); self.assertTrue(all(m['enabled'] for m in public['models']))
        self.assertEqual(self.toggle_model('legacy-a', False).status_code, 200)
        resolved = main._resolve_configs('llm', [{'id':self.id}])[0]
        self.assertEqual(resolved['model'], 'legacy-b'); self.assertEqual(resolved['base_url'], legacy['base_url'])
        self.assertEqual(self.toggle_provider(False).status_code, 200); self.assertEqual(self.toggle_provider(True).status_code, 200)
        self.assertFalse(configured_models(self.store.get_service_config(self.id))[0]['enabled'])

    def test_provider_group_updates_all_legacy_configs_but_not_other_providers(self):
        config = self.store.get_service_config(self.id)
        self.store.put_service_config({**config,'id':'other-vllm'})
        self.store.put_service_config({**config,'id':'other-provider','provider':'deepseek'})
        response = self.toggle_provider(False)
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(len(response.json()),2)
        self.assertFalse(self.store.get_service_config('other-vllm')['enabled'])
        self.assertTrue(self.store.get_service_config('other-provider')['enabled'])

    def test_concurrent_model_and_parent_toggles_do_not_lose_updates(self):
        with ThreadPoolExecutor(max_workers=3) as workers:
            tasks = [workers.submit(self.store.set_model_enabled,self.id,alias,False) for alias in ['primary','standby']]
            tasks.append(workers.submit(self.store.set_provider_enabled,'vllm',False))
            for task in tasks: task.result()
        saved = self.store.get_service_config(self.id)
        self.assertFalse(saved['enabled']); self.assertFalse(saved['models'][0]['enabled']); self.assertFalse(saved['models'][1]['enabled'])
        self.assertTrue(saved['models'][2]['enabled'])

    def test_invalid_or_missing_targets_do_not_change_config(self):
        before = self.store.get_service_config(self.id)
        for value in [None, 'false', 0, {}]:
            self.assertEqual(self.client.patch('/api/provider-models/vllm/enabled',json={'enabled':value}).status_code,422)
        self.assertEqual(self.toggle_model('missing',False).status_code,404)
        self.assertEqual(self.client.patch('/api/provider-models/missing/enabled',json={'enabled':False}).status_code,404)
        self.assertEqual(self.client.patch('/api/service-configs/missing/models/enabled',json={'model':'primary','enabled':False}).status_code,404)
        self.assertEqual(self.store.get_service_config(self.id),before)
