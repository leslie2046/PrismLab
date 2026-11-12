"""Exact request inputs and original context remain bound to their own samples."""
import json
import unittest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock

from fastapi import HTTPException
from backend.dataset_store import DatasetStore
from backend.dataset_trace import DatasetTraceStore
from tests.database import create_test_store
from backend.dataset_build_control import BuildControl, BuildRepository, build_state
from backend.dataset_golden import DatasetItem, golden_dict
from backend.dataset_trace import GenerationTrace
from backend.datasets import build_dataset
from backend.deepeval_generation import WorkspaceLLM
from shared.llm_stream import stream_chat
from shared.request_control import GenerationStopped
from tests import test_datasets as fixtures
from tests.test_deepeval_datasets import SDKFixture


class DatasetTraceTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp

    def dataset(self, size=2):
        return self.repo.create(dict(name='Trace fixture', status='generating', source_type='documents',
            target_size=size, build_target=size, build_progress=0, generation_options={'generation_backend':'deepeval','num_evolutions':0},
            **build_state()))

    def generate(self, dataset):
        sdk = SDKFixture()
        def post(url, **kwargs):
            payload = kwargs['json']
            _, raw = sdk(url, payload['model'], None, payload['messages'][-1]['content'])
            response = MagicMock()
            response.__enter__.return_value = response
            response.iter_lines.return_value = [
                'data: '+json.dumps({'choices':[{'delta':{'content':raw},'finish_reason':'stop'}],'usage':{'completion_tokens':11}}),
                'data: [DONE]',
            ]
            return response
        with patch('shared.llm_stream.requests.post', side_effect=post) as http:
            build_dataset(self.repo, dataset['id'], [{'id':'chunk','content':'Original source context.','metadata':{'filename':'source.txt'}}],
                None, {**self.config, 'base_url':'https://secret-provider.example/path'}, 'private-api-key', 'Original generation instructions', build_token=dataset['build_token'])
        self.assertEqual(self.repo.get(dataset['id'])['status'], 'ready')
        return self.repo.items(dataset['id'])['items'], http

    def endpoint(self, dataset, item, tail=''):
        return f"/api/datasets/{dataset['id']}/items/{item['id']}/generation"+tail

    def test_actual_sdk_messages_and_original_context_are_saved_for_each_sample(self):
        self.config = {**self.config, 'provider':'siliconflow', 'model':'Qwen/Qwen3.5-27B'}
        dataset = self.dataset()
        items, http = self.generate(dataset)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item['generation_trace_id'] for item in items}), 1)
        self.assertEqual(len({item['generation_record_id'] for item in items}), 2)
        sent = [call.kwargs['json'] for call in http.call_args_list]
        detail = self.client.get(self.endpoint(dataset, items[0])).json()
        self.assertEqual(detail['availability'], 'recorded')
        self.assertFalse(detail['edited'])
        self.assertEqual(detail['trace']['prompt'], 'Original generation instructions')
        self.assertEqual(detail['trace']['sources'][0]['content'], 'Original source context.')
        self.assertEqual(detail['sample']['context'], items[0]['context'])
        page = self.client.get(self.endpoint(dataset, items[0], '/requests?page_size=100')).json()
        captured = [self.client.get(self.endpoint(dataset, items[0], '/requests/'+r['id'])).json()['request'] for r in page['items']]
        # SDK calls can run concurrently, so compare the exact payload multiset.
        self.assertCountEqual([json.dumps(p, sort_keys=True) for p in captured], [json.dumps(p, sort_keys=True) for p in sent])
        self.assertIn('Original source context.', json.dumps(captured))
        self.assertIn('JSON Schema', json.dumps(captured))
        self.assertTrue(all(p['enable_thinking'] is False for p in captured))
        self.assertEqual({row['role'] for row in page['items']}, {'generation', 'quality_review'})
        serialized = json.dumps({'detail':detail,'requests':captured})
        self.assertNotIn('private-api-key', serialized)
        self.assertNotIn('secret-provider.example', serialized)
        self.assertNotIn('generation_trace_id', golden_dict(items[0]))
        self.assertNotIn('generation_record_id', golden_dict(items[0]))

    def test_edit_and_source_sync_do_not_rewrite_original_generation(self):
        dataset = self.dataset()
        items, _ = self.generate(dataset)
        item = items[0]
        before = self.client.get(self.endpoint(dataset, item)).json()
        self.repo.save_sources(dataset['id'], [{'content':'New synced source','metadata':{}}])
        self.repo.mutate(dataset['id'], lambda c, d: d.update(prompt='New prompt', generation_config={'task':'New task'}))
        response = self.client.put(f"/api/datasets/{dataset['id']}/items/{item['id']}", json={'input':'Edited question','expected_output':'Edited answer','context':['Edited context']})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.client.get(self.endpoint(dataset, item)).json()
        self.assertTrue(after['edited'])
        self.assertEqual(before['trace'], after['trace'])
        self.assertEqual(before['sample'], after['sample'])
        self.assertEqual(after['trace']['sources'][0]['content'], 'Original source context.')

    def test_history_without_record_never_uses_current_prompt_or_sources(self):
        dataset = self.repo.create({'name':'Legacy', 'prompt':'New current prompt'})
        self.repo.add(dataset['id'], [DatasetItem(input='Legacy question', context=['Saved legacy context']).model_dump()])
        item = self.repo.items(dataset['id'])['items'][0]
        response = self.client.get(self.endpoint(dataset, item))
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail['availability'], 'unavailable')
        self.assertEqual(detail['sample']['context'], ['Saved legacy context'])
        self.assertIsNone(detail['trace'])
        self.assertNotIn('New current prompt', response.text)
        self.assertEqual(self.client.get(self.endpoint(dataset,item,'/requests')).json()['total'],0)

    def test_trace_endpoints_enforce_dataset_and_request_ownership(self):
        first, second = self.dataset(), self.dataset()
        a, _ = self.generate(first)
        b, _ = self.generate(second)
        request = self.client.get(self.endpoint(first,a[0],'/requests')).json()['items'][0]
        self.assertEqual(self.client.get(self.endpoint(second,a[0])).status_code,404)
        self.assertEqual(self.client.get(self.endpoint(second,b[0],'/requests/'+request['id'])).status_code,404)
        self.assertEqual(self.client.get('/api/datasets/missing/items/missing/generation').status_code,404)
        self.assertEqual(self.client.get(self.endpoint(first,a[0],'/requests?page=0')).status_code,422)
        self.assertEqual(self.client.get(self.endpoint(first,a[0],'/requests?page=999')).json()['page'],1)

    def test_cancelled_worker_cannot_save_more_trace_records_or_issue_model_calls(self):
        dataset = self.dataset()
        guarded = BuildRepository(self.repo,dataset['build_token'])
        trace = GenerationTrace(guarded,dataset['id'],{'prompt':'Saved','sources':[]})
        self.assertEqual(self.client.post(f"/api/datasets/{dataset['id']}/cancel").status_code,200)
        with self.assertRaises(GenerationStopped):
            trace.request({'messages':[{'role':'user','content':'Late request'}]},'generation')
        with patch('shared.llm_stream.requests.post') as http:
            control = BuildControl(self.repo,dataset['id'],dataset['build_token']);control.trace=trace
            with self.assertRaises(GenerationStopped):
                stream_chat('unused','model',None,'Late request',_control=control)
        http.assert_not_called()
        with self.store._session() as connection:
            self.assertEqual(connection.execute('SELECT count(*) AS n FROM dataset_generation_records').fetchone()['n'],0)

    def test_concurrent_requests_capture_explicit_control_and_are_paginated(self):
        dataset = self.dataset()
        control = BuildControl(self.repo,dataset['id'],dataset['build_token'])
        trace = GenerationTrace(BuildRepository(self.repo,dataset['build_token']),dataset['id'],{'prompt':'Snapshot','sources':[]})
        control.trace=trace
        # Explicit controls cover SDK callbacks that start on independent threads.
        def call(index):
            response = MagicMock();response.__enter__.return_value=response
            response.iter_lines.return_value=['data: '+json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]}),'data: [DONE]']
            return response
        with patch('shared.llm_stream.requests.post',side_effect=lambda *args,**kwargs:call(0)):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda n:stream_chat('fixture','model',None,f'Exact input {n}',_control=control,system_prompt='System rules'),range(12)))
        values = trace.attach([DatasetItem(input='Question').model_dump()])
        self.repo.add(dataset['id'],values,generating=True)
        item=self.repo.items(dataset['id'])['items'][0]
        page=self.client.get(self.endpoint(dataset,item,'/requests?page=2&page_size=5')).json()
        self.assertEqual(page['total'],12)
        self.assertEqual([int(row['sequence']) for row in page['items']],list(range(6,11)))
        self.assertNotIn('Exact input',json.dumps(page))
        full=self.client.get(self.endpoint(dataset,item,'/requests/'+page['items'][0]['id'])).json()
        self.assertEqual(full['request']['messages'][0],{'role':'system','content':'System rules'})

    def test_public_edits_cannot_forge_trace_links_and_dataset_delete_removes_traces(self):
        dataset=self.dataset();items,_=self.generate(dataset)
        response=self.client.put(f"/api/datasets/{dataset['id']}/items/{items[0]['id']}",json={'input':'Forged','generation_trace_id':'unrelated'})
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.delete(f"/api/datasets/{dataset['id']}").status_code,204)
        with self.store._session() as connection:
            for table in ['dataset_generation_records','dataset_generation_traces']:
                self.assertEqual(connection.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],0)

    def test_different_batches_only_expose_their_own_requests_and_source_snapshots(self):
        dataset = self.dataset(size=6)
        chunks = [{'id':str(i), 'content':f'Source {i}', 'metadata':{}} for i in range(3)]
        def generate(batch, count, *args):
            stream_chat('fixture', 'model', None, 'Actual SDK input: '+batch[0]['content'])
            return [DatasetItem(input=f'{batch[0]["content"]} question {i}', context=[batch[0]['content']]).model_dump() for i in range(count)]
        response=MagicMock();response.__enter__.return_value=response
        def lines(**kwargs):
            return iter(['data: '+json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]}),'data: [DONE]'])
        response.iter_lines.side_effect=lines
        with patch('backend.datasets.generate_items',side_effect=generate), patch('shared.llm_stream.requests.post',return_value=response):
            build_dataset(self.repo,dataset['id'],chunks,None,self.config,'private-key','Task',build_token=dataset['build_token'])
        items=self.repo.items(dataset['id'])['items']
        self.assertEqual(len(items),6)
        self.assertEqual(len({item['generation_trace_id'] for item in items}),2)
        a=next(item for item in items if item['input'].startswith('Source 0'))
        b=next(item for item in items if item['input'].startswith('Source 1'))
        record=self.client.get(self.endpoint(dataset,a,'/requests')).json()['items'][0]
        self.assertEqual(self.client.get(self.endpoint(dataset,b,'/requests/'+record['id'])).status_code,404)
        self.assertEqual(self.client.get(self.endpoint(dataset,a)).json()['trace']['sources'][0]['content'],'Source 0')
        self.assertEqual(self.client.get(self.endpoint(dataset,b)).json()['trace']['sources'][0]['content'],'Source 1')

    def test_trace_reads_cannot_cross_workspace_schemas(self):
        dataset=self.dataset();items,_=self.generate(dataset)
        with tempfile.TemporaryDirectory() as temp:
            other=DatasetStore(create_test_store(self,Path(temp)))
        with self.assertRaises(HTTPException) as error:
            DatasetTraceStore(other).get(dataset['id'],items[0]['id'])
        self.assertEqual(error.exception.status_code,404)

    def test_cancellation_after_request_record_prevents_transport_start(self):
        dataset=self.dataset()
        trace=GenerationTrace(BuildRepository(self.repo,dataset['build_token']),dataset['id'],{'prompt':'Original','sources':[]})
        control=BuildControl(self.repo,dataset['id'],dataset['build_token']);control.trace=trace
        record=trace.request
        def cancel_after_record(*args):
            value=record(*args)
            self.client.post(f"/api/datasets/{dataset['id']}/cancel")
            return value
        with patch.object(trace,'request',side_effect=cancel_after_record),patch('shared.llm_stream.requests.post') as http:
            with self.assertRaises(GenerationStopped):
                stream_chat('fixture','model',None,'Prompt',_control=control)
        http.assert_not_called()

    def test_request_keeps_original_batch_binding_when_control_moves_on(self):
        dataset=self.dataset()
        repo=BuildRepository(self.repo,dataset['build_token'])
        first=GenerationTrace(repo,dataset['id'],{'prompt':'First batch','sources':[]})
        second=GenerationTrace(repo,dataset['id'],{'prompt':'Second batch','sources':[]})
        control=BuildControl(self.repo,dataset['id'],dataset['build_token']);control.trace=first
        execute=control.execute
        def move_to_next(operation,timeout):
            control.trace=second
            return execute(operation,timeout)
        response=MagicMock();response.__enter__.return_value=response
        response.iter_lines.return_value=['data: '+json.dumps({'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]}),'data: [DONE]']
        with patch.object(control,'execute',side_effect=move_to_next),patch('shared.llm_stream.requests.post',return_value=response):
            stream_chat('fixture','model',None,'First batch prompt',_control=control)
        with self.store._session() as connection:
            rows=connection.execute("SELECT trace_id FROM dataset_generation_records WHERE kind='request'").fetchall()
        self.assertEqual([row['trace_id'] for row in rows],[first.id])
