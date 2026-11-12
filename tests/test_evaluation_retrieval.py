"""Actual multi-route execution, ranking, budgets and immutable configuration."""
from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

import requests

from backend.dataset_annotations import item_hash
from tests import test_evaluations as fixtures
from tests import test_evaluation_execution as execution


def reply(data, status=200):
    response = MagicMock(status_code=status, content=b'{}')
    response.json.return_value = data
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError('private-key private-url', response=response)
    return response


class MultiRetrievalTests(unittest.TestCase):
    prepare = fixtures.EvaluationTests.prepare
    execute = execution.ExecutionTests.execute

    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        self.first = {'id':'a', 'model':None, 'credential_id':None}
        self.second = {'id':'b', 'model':None, 'credential_id':None}
        self.reranker = {'id':'rr', 'model':'rank-model', 'credential_id':None}
        self.configs = {key: {**self.config, 'name':'Knowledge '+key, 'model':None,
            'base_url':'https://fixture.invalid/'+key, 'dataset_id':'external-'+key} for key in ('a','b')}
        self.configs['rr'] = {**self.config, 'name':'Ranker', 'model':'rank-model',
                              'base_url':'https://fixture.invalid/rerank'}
        def resolve(request):
            self.assertEqual(request.benchmark, 'reranker' if request.provider_id=='rr' else 'dify-retrieve')
            return self.configs[request.provider_id], 'private-key'
        self.resolve.side_effect = resolve
        self.request.update(evaluation_type='retrieval', template='retrieval', scoring_engine='rules', judge=None,
            metrics=[{'id':'rules.recall_at_k','k':2},{'id':'rules.mrr','k':2}],
            budget={'max_requests':100,'max_output_tokens':10000,'max_retries':2},
            target={'benchmark':'dify-retrieve','selection':self.first,'selections':[self.first,self.second],
                    'retrieval_reranker':self.reranker,'retrieval_top_k':2,
                    'retrieval_candidate_top_k':3,'retrieval_score_threshold':.5})
        item = self.repo.items(self.dataset_id)['items'][0]
        annotation = {'content_hash':item_hash(item), 'review_status':'approved',
                      'relevant_chunk_ids':['b1','a2'], 'graded_relevance':{'b1':3,'a2':2}}
        with self.store._session() as c:
            c.execute('INSERT INTO dataset_annotations VALUES (%s,%s,%s)',(item['id'],self.dataset_id,json.dumps(annotation)))
        self.posts=[]
        self.barrier=None

    def post(self, url, **kwargs):
        self.posts.append((url,kwargs['json']))
        if url.endswith('/rerank'):
            return reply({'results':[{'index':1,'relevance_score':.6},{'index':2,'relevance_score':.9}]})
        if self.barrier:
            self.barrier.wait(timeout=3)
        source = 'a' if '/external-a/' in url else 'b'
        values = [('a1','A first',.99),('a2','A second',.7)] if source=='a' else [('b1','B first',.1)]
        return reply({'records':[{'segment':{'id':id,'content':text,'document_id':'doc'},'score':score} for id,text,score in values]})

    def build(self):
        with patch('backend.evaluation_execution.requests.get',return_value=execution.dify_settings_response()) as get, \
             patch('backend.evaluation_execution.requests.post',side_effect=self.post) as post:
            run=self.execute(self.prepare())
        return run,get,post

    def test_parallel_recall_global_ranking_real_ids_and_provenance(self):
        self.barrier=threading.Barrier(2)
        run,get,post=self.build()
        self.assertEqual(run['status'],'completed',run)
        observation=run['cases'][0]['observation']
        self.assertEqual(observation['retrieved_chunk_ids'],['b1','a2'])
        self.assertEqual(observation['retrieval_context'],['B first','A second'])
        self.assertEqual([r['score'] for r in run['metric_results']],[1,1])
        self.assertEqual([s['config_id'] for s in observation['metadata']['sources']],['b','a'])
        self.assertEqual([s['score'] for s in observation['metadata']['sources']],[.9,.6])
        self.assertEqual(get.call_count,2)
        self.assertEqual(run['usage']['target']['requests'],4)
        self.assertEqual(run['usage']['reranker']['requests'],1)
        rank=next(body for url,body in self.posts if url.endswith('/rerank'))
        self.assertEqual(rank['documents'],['A first','A second','B first'])
        self.assertEqual(rank['top_n'],2)
        for url,body in self.posts:
            if not url.endswith('/rerank'):
                self.assertEqual(body['retrieval_model']['top_k'],3)
                self.assertFalse(body['retrieval_model']['score_threshold_enabled'])
                self.assertEqual(body['retrieval_model']['search_method'],'hybrid_search')
        self.assertNotIn('private-key',json.dumps(run))

    def test_threshold_can_yield_empty_valid_retrieval(self):
        self.request['target']['retrieval_score_threshold']=.95
        run,_,_=self.build()
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['cases'][0]['observation']['retrieved_chunk_ids'],[])
        self.assertTrue(all(r['score']==0 for r in run['metric_results']))

    def test_vllm_long_candidates_use_server_token_limit_and_preserve_full_evidence(self):
        original_text = '知识库原始片段。' * 200
        post = self.post
        def limited_model(url, **kwargs):
            if url.endswith('/rerank'):
                self.assertIn(original_text, kwargs['json']['documents'])
                if kwargs['json'].get('truncate_prompt_tokens') != -1:
                    return reply({'error': {'message': "Maximum context length is 512 tokens; input has 513 tokens"}}, 400)
            response = post(url, **kwargs)
            if '/external-a/' in url:
                response.json()['records'][1]['segment']['content'] = original_text
            return response
        with patch.object(self, 'post', side_effect=limited_model):
            run, _, _ = self.build()
        self.assertEqual(run['status'], 'completed', run)
        observation = run['cases'][0]['observation']
        self.assertIn(original_text, observation['retrieval_context'])
        self.assertEqual(observation['metadata']['retrieval_strategy']['reranker_parameters'],
                         {'truncate_prompt_tokens': -1})
        self.assertEqual(run['usage']['reranker']['requests'], 1)

    def test_other_rerank_providers_do_not_receive_vllm_parameters(self):
        self.configs['rr']['provider'] = 'siliconflow'
        run, _, _ = self.build()
        self.assertEqual(run['status'], 'completed', run)
        rank = next(body for url, body in self.posts if url.endswith('/rerank'))
        self.assertNotIn('truncate_prompt_tokens', rank)
        self.assertEqual(run['cases'][0]['observation']['metadata']['retrieval_strategy']['reranker_parameters'], {})

    def test_reranker_rejections_are_actionable_safe_and_not_retried(self):
        post = self.post
        for index, (status, data, code) in enumerate([
            (400, {'error': {'message': 'Maximum context length is 512 tokens; input has 513 tokens private-key'}}, 'reranker_input_too_long'),
            (422, {'detail': 'input length exceeds maximum private-key'}, 'reranker_input_too_long'),
            (400, {'error': {'message': 'private-key private-url model not found'}}, 'reranker_request_rejected'),
            (404, {'detail': 'private-url endpoint not found'}, 'reranker_request_rejected'),
        ]):
            with self.subTest(status=status, code=code):
                self.request['idempotency_key'] = 'rerank-rejected-' + str(index)
                def rejected(url, **kwargs):
                    return reply(data, status) if url.endswith('/rerank') else post(url, **kwargs)
                with patch.object(self, 'post', side_effect=rejected):
                    run, _, http = self.build()
                self.assertEqual(run['status'], 'failed', run)
                self.assertEqual(run['error'], code)
                self.assertEqual(run['usage']['reranker']['requests'], 1)
                self.assertEqual(sum(call.args[0].endswith('/rerank') for call in http.call_args_list), 1)
                self.assertEqual(run['cases'][0]['error'], code)
                self.assertIsNone(run['cases'][0]['observation'])
                self.assertTrue(all(r['score'] is None and r['error'] == code for r in run['metric_results']))
                self.assertNotIn('private-key', json.dumps(run))
                self.assertNotIn('private-url', json.dumps(run))

    def test_failure_of_one_route_never_scores_partial_results(self):
        post=self.post
        def fail(url,**kwargs):
            return reply({},503) if '/external-b/' in url else post(url,**kwargs)
        with patch.object(self,'post',side_effect=fail):
            run,_,http=self.build()
        self.assertEqual(run['status'],'failed',run)
        self.assertEqual(run['error'],'service_unavailable')
        self.assertFalse(any(call.args[0].endswith('/rerank') for call in http.call_args_list))
        self.assertIsNone(run['cases'][0]['observation'])
        self.assertTrue(all(r['score'] is None for r in run['metric_results']))

    def test_global_reranker_counts_against_budget(self):
        self.request['budget']['max_requests']=4
        run,_,http=self.build()
        self.assertEqual(run['error'],'budget_exhausted',run)
        self.assertFalse(any(call.args[0].endswith('/rerank') for call in http.call_args_list))
        self.assertTrue(all(r['score'] is None for r in run['metric_results']))

    def test_invalid_rank_indices_and_scores_are_rejected(self):
        post=self.post
        for rows in ([{'index':99,'relevance_score':.9}],
                     [{'index':True,'relevance_score':.9}],
                     [{'index':0,'relevance_score':float('nan')}],
                     [{'index':0,'relevance_score':.9},{'index':0,'relevance_score':.8}]):
            self.request['idempotency_key']='invalid-'+str(len(self.evaluations.list()))
            def invalid(url,**kwargs):
                return reply({'results':rows}) if url.endswith('/rerank') else post(url,**kwargs)
            with patch.object(self,'post',side_effect=invalid):
                run,_,_=self.build()
            self.assertEqual(run['error'],'invalid_target_response',run)
            self.assertIsNone(run['cases'][0]['observation'])

    def test_default_reranker_is_frozen_and_explicit_override_wins(self):
        self.store.save_system_settings({'evaluation_default_reranker':self.reranker})
        self.request['target'].pop('retrieval_reranker')
        prepared=self.prepare()
        before=deepcopy(prepared['evidence'])
        self.assertEqual(before['target']['retrieval_reranker']['model'],'rank-model')
        self.store.save_system_settings({'evaluation_default_reranker':None})
        self.assertEqual(self.evaluations.get(prepared['id'])['evidence'],before)
        self.request['idempotency_key']='override'
        self.request['target']['retrieval_reranker']=self.reranker
        self.assertEqual(self.prepare()['evidence']['target']['retrieval_reranker']['model'],'rank-model')

    def test_missing_reranker_duplicates_and_out_of_range_fail_before_execution(self):
        target=deepcopy(self.request['target'])
        for field,value in [('retrieval_top_k',0),('retrieval_score_threshold',1.1),
                            ('retrieval_candidate_top_k',1),('selections',[self.first,self.first])]:
            self.request['target']={**target,field:value}
            self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,422)
        self.request['target']={**target,'retrieval_reranker':None}
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,400)
        self.request['target']={**target,'benchmark':'llm'}
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,422)
        self.request['target']=target
        self.configs['b']={**self.configs['a']}
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,400)

    def test_changed_secondary_source_or_reranker_prevents_any_request(self):
        for key in ('b','rr'):
            self.request['idempotency_key']='changed-'+key
            prepared=self.prepare()
            self.configs[key]['updated_at']='changed'
            with patch('backend.evaluation_execution.requests.get') as get,patch('backend.evaluation_execution.requests.post') as post:
                run=self.execute(prepared)
            self.assertEqual(run['error'],'configuration_changed',run)
            get.assert_not_called();post.assert_not_called()
            self.configs[key]['updated_at']=self.config.get('updated_at')

    def test_default_settings_roundtrip_uses_reranker_endpoint_type(self):
        result=self.client.put('/api/dataset-settings',json={'default_reranker':self.reranker})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['default_reranker'],self.reranker)
        self.assertEqual(self.resolve.call_args.args[0].benchmark,'reranker')
        self.assertEqual(self.client.get('/api/dataset-settings').json()['default_reranker'],self.reranker)
        cleared=self.client.put('/api/dataset-settings',json={'default_reranker':None})
        self.assertIsNone(cleared.json()['default_reranker'])

    def test_cancel_during_rerank_discards_late_observation(self):
        prepared=self.prepare()
        post=self.post
        def cancel(url,**kwargs):
            if url.endswith('/rerank'):
                self.evaluations.cancel(prepared['id'])
            return post(url,**kwargs)
        with patch('backend.evaluation_execution.requests.get',return_value=execution.dify_settings_response()), \
             patch('backend.evaluation_execution.requests.post',side_effect=cancel):
            run=self.execute(prepared)
        self.assertEqual(run['status'],'cancelled')
        self.assertIsNone(run['cases'][0]['observation'])

    def test_settings_loaded_once_and_reranker_called_for_each_sample(self):
        self.repo.add(self.dataset_id,[{'input':'Second question','expected_output':'Reference'}])
        run,get,_=self.build()
        self.assertEqual(get.call_count,2)
        self.assertEqual(run['usage']['target']['requests'],6)
        self.assertEqual(run['usage']['reranker']['requests'],2)
        self.assertTrue(all(c['observation']['retrieved_chunk_ids']==['b1','a2'] for c in run['cases']))

    def test_empty_routes_skip_reranker_and_record_zero_recall(self):
        with patch.object(self,'post',return_value=reply({'records':[]})):
            run,_,http=self.build()
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(http.call_count,2)
        self.assertTrue(all(r['score']==0 for r in run['metric_results']))
        self.assertEqual(run['cases'][0]['observation']['retrieval_context'],[])

    def test_duplicate_segment_is_not_sent_twice_to_reranker(self):
        post=self.post
        def duplicate(url,**kwargs):
            response=post(url,**kwargs)
            if '/external-a/' in url:
                records=response.json()['records']
                records.insert(1,deepcopy(records[0]))
            return response
        with patch.object(self,'post',side_effect=duplicate):
            run,_,_=self.build()
        rank=next(body for url,body in self.posts if url.endswith('/rerank'))
        self.assertEqual(rank['documents'],['A first','A second','B first'])
        self.assertEqual(run['cases'][0]['observation']['retrieved_chunk_ids'],['b1','a2'])

    def test_nested_reranker_output_preserves_index_mapping(self):
        post=self.post
        def nested(url,**kwargs):
            response=post(url,**kwargs)
            if url.endswith('/rerank'):
                response.json.return_value={'output':response.json.return_value,'usage':{'input_tokens':20}}
            return response
        with patch.object(self,'post',side_effect=nested):
            run,_,_=self.build()
        self.assertEqual(run['cases'][0]['observation']['retrieved_chunk_ids'],['b1','a2'])
        self.assertEqual(run['usage']['reranker']['input_tokens'],20)

    def test_single_base_without_reranker_filters_original_scores(self):
        self.request['target'].update(selections=[self.first],retrieval_reranker=None,retrieval_score_threshold=.8)
        run,_,post=self.build()
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(post.call_count,1)
        observation=run['cases'][0]['observation']
        self.assertEqual(observation['retrieved_chunk_ids'],['a1'])
        self.assertEqual(observation['metadata']['retrieval_strategy']['mode'],'single_source')
