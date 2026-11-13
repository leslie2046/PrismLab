"""SDK execution and durable-job acceptance with deterministic model fixtures."""
import json
import threading
import time
import unittest
from copy import deepcopy
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from backend.evaluation_execution import run_evaluation, score_rule, score_metric, RequestTracker
from backend.evaluation_results import summarize
from tests.test_evaluations import EvaluationTests as BaseFixture


def dify_settings_response():
    response = unittest.mock.MagicMock(status_code=200, content=b'{}')
    response.json.return_value = {'retrieval_model_dict': {
        'search_method': 'hybrid_search', 'top_k': 10, 'reranking_enable': True,
        'reranking_mode': 'reranking_model', 'reranking_model': {
            'reranking_provider_name': 'fixture', 'reranking_model_name': 'reranker'},
        'score_threshold_enabled': True, 'score_threshold': 0.25, 'weights': None}}
    return response


class JudgeTransportTests(unittest.TestCase):
    def test_judge_wire_disables_only_supported_reasoning_and_keeps_token_budget(self):
        for provider, model, controls in [
            ('siliconflow','Qwen/Qwen3.5-27B',{'enable_thinking':False}),
            ('vllm','Qwen3-8B',{'chat_template_kwargs':{'enable_thinking':False}}),
            ('deepseek','deepseek-flash',{'thinking':{'type':'disabled'}}),
            ('siliconflow','unknown-model',{}),
        ]:
            with self.subTest(provider=provider,model=model):
                repo=unittest.mock.MagicMock()
                tracker=RequestTracker(repo,'run','token',{'judge_max_tokens':1024,'max_retries':2})
                response=unittest.mock.MagicMock()
                response.__enter__.return_value=response
                response.iter_lines.return_value=[
                    'data: '+json.dumps({'choices':[{'delta':{'content':'{"reason":"Supported"}'},'finish_reason':'stop'}]}),
                    'data: [DONE]',
                ]
                with patch('shared.llm_stream.requests.post',return_value=response) as post:
                    answer=tracker.judge_text({'provider':provider,'model':model,'base_url':'https://fixture.invalid/chat/completions'},
                                              'private-key','Judge this answer','Evaluate the evidence')
                self.assertEqual(answer,'{"reason":"Supported"}')
                body=post.call_args.kwargs['json']
                self.assertEqual(body['max_tokens'],1024)
                self.assertEqual({k:body[k] for k in ('enable_thinking','chat_template_kwargs','thinking') if k in body},controls)
                repo.reserve_request.assert_called_once_with('run','token','judge',1024)
                self.assertEqual(tracker.stage_usage['inference_parameters'],{'max_tokens':1024,**controls})


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        BaseFixture.setUp(self)
        self.request['budget']={'max_requests':100, 'max_output_tokens':1000000}

    def prepare(self):
        response=self.client.post('/api/evaluation-runs',json=self.request)
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    def execute(self,run):
        self.evaluations.enqueue(run['id'])
        claim=self.evaluations.claim('test-worker')
        self.assertIsNotNone(claim)
        run_evaluation(self.evaluations,claim,self.resolve)
        return self.evaluations.get(run['id'])

    def test_evaluation_concurrency_is_frozen_from_workspace_settings(self):
        self.store.save_system_settings({'evaluation_max_concurrent': 4})
        prepared = self.prepare()
        self.assertEqual(prepared['evidence']['budget']['evaluation_max_concurrent'], 4)

    def test_samples_are_processed_concurrently_with_workspace_limit(self):
        self.request.update(metrics=[], template='retrieve_only')
        self.store.save_system_settings({'evaluation_max_concurrent': 3})
        self.repo.add(self.dataset_id, [{'input': f'Question {index}', 'expected_output': 'Reference'} for index in range(4)])
        active = 0
        peak = 0
        lock = threading.Lock()

        def target(*_args, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {'actual_output': 'Answer', 'retrieval_context': None, 'retrieved_chunk_ids': None,
                    'metadata': {}, 'duration_ms': 1, 'usage': {}}

        with patch('backend.evaluation_execution.call_target', side_effect=target):
            run = self.execute(self.prepare())
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(len(run['cases']), 5)
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 3)

    def test_retired_http_run_fails_without_resolving_or_calling_target(self):
        repo = unittest.mock.MagicMock()
        repo.get.return_value = {'evidence': {'target': {'benchmark': 'http', 'selection': {'id': 'retired'}}}}
        resolve = unittest.mock.MagicMock()
        with patch('backend.evaluation_execution.requests.post') as post, patch('backend.evaluation_execution.stream_chat') as chat:
            run_evaluation(repo, {'id': 'legacy', 'worker_token': 'worker'}, resolve)
        repo.abort_pending.assert_called_once_with('legacy', 'worker', 'unsupported_target')
        resolve.assert_not_called()
        post.assert_not_called()
        chat.assert_not_called()

    def test_actual_llm_and_official_deepeval_answer_relevancy(self):
        self.request['metrics']=[{'id':'deepeval.answer_relevancy'}]
        def model(endpoint,name,key,prompt,**kwargs):
            if prompt=='Question': text='The answer is grounded.'
            else:
                title=json.loads(prompt.split('Return JSON matching this schema:\n')[-1])['title']
                if title=='Statements': text=json.dumps({'statements':['The answer is grounded.']})
                elif title=='Verdicts': text=json.dumps({'verdicts':[{'verdict':'yes','reason':'Relevant to the question'}]})
                else: text=json.dumps({'reason':'The answer addresses the question.'})
            return {'output_tokens':20},text
        with patch('backend.evaluation_execution.stream_chat',side_effect=model):
            run=self.execute(self.prepare())
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['cases'][0]['observation']['actual_output'],'The answer is grounded.')
        self.assertEqual(run['metric_results'][0]['score'],1)
        self.assertGreater(run['usage']['judge']['requests'],0)
        self.assertEqual(summarize(run)['counts']['not_passed'],0)

    def test_deepeval_judge_uses_frozen_nonthinking_policy_and_model_output_limit(self):
        self.config.update(provider='siliconflow', model='Qwen/Qwen3.5-27B')
        self.request['metrics']=[{'id':'deepeval.answer_relevancy'}]
        with patch('backend.llm_parameters._metadata', return_value={'max_output_tokens':512}):
            prepared=self.prepare()
        expected={'max_tokens':512,'enable_thinking':False}
        self.assertEqual(prepared['evidence']['result_judge']['inference_parameters'],expected)
        target={'actual_output':'Relevant answer','retrieval_context':None,'duration_ms':1,'metadata':{}}
        def model(endpoint,name,key,prompt,**kwargs):
            self.assertEqual(kwargs['request_parameters'],expected)
            self.assertEqual(kwargs['max_tokens'],512)
            title=json.loads(prompt.split('Return JSON matching this schema:\n')[-1])['title']
            if title=='Statements':text=json.dumps({'statements':['Relevant answer']})
            elif title=='Verdicts':text=json.dumps({'verdicts':[{'verdict':'yes','reason':'Relevant'}]})
            else:text=json.dumps({'reason':'Relevant answer'})
            return {'input_tokens':5,'output_tokens':10},text
        with patch('backend.evaluation_execution.call_target',return_value=target), \
             patch('backend.evaluation_execution.generation_parameters',side_effect=AssertionError('Frozen parameters must be used')), \
             patch('backend.evaluation_execution.stream_chat',side_effect=model):
            run=self.execute(prepared)
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['metric_results'][0]['usage']['inference_parameters'],expected)
        usage=run['usage']['judge']
        self.assertEqual(usage['reserved_output_tokens'],usage['requests']*512)

    def test_legacy_running_judge_uses_nonthinking_policy_without_repeating_saved_target(self):
        self.config.update(provider='siliconflow', model='Qwen/Qwen3.5-27B')
        prepared=self.prepare()
        self.evaluations.enqueue(prepared['id'])
        claim=self.evaluations.claim('legacy-judge')
        self.evaluations.record_target(prepared['id'],prepared['cases'][0]['id'],claim['worker_token'],
            observation={'actual_output':'saved answer','duration_ms':1})
        # Simulate an older immutable snapshot at the repository boundary.
        get=self.evaluations.get
        def legacy(run_id):
            run=get(run_id)
            run['evidence']['result_judge'].pop('inference_parameters',None)
            return run
        def score(metric,sample,observation,judge,config,key,tracker):
            tracker.judge_text(config,key,'Return JSON','Evaluate')
            return .8,'Supported'
        with patch.object(self.evaluations,'get',side_effect=legacy), \
             patch('backend.evaluation_execution.call_target') as target, \
             patch('backend.evaluation_execution.score_metric',side_effect=score), \
             patch('backend.evaluation_execution.stream_chat',return_value=({'output_tokens':5},'{"reason":"Supported"}')) as chat:
            run_evaluation(self.evaluations,claim,self.resolve)
        target.assert_not_called()
        self.assertTrue(all(c.kwargs['request_parameters']=={'max_tokens':2048,'enable_thinking':False} for c in chat.call_args_list))
        self.assertEqual(get(prepared['id'])['status'],'completed')

    def test_actual_ragas_faithfulness_sdk_and_empty_actual_retrieval(self):
        self.config.update(provider='siliconflow', model='Qwen/Qwen3.5-27B')
        self.request.update(scoring_engine='ragas',metrics=[{'id':'ragas.faithfulness'}])
        def model(endpoint,name,key,prompt,**kwargs):
            if prompt=='Question': return {'output_tokens':3},'Supported answer'
            self.assertEqual(kwargs['request_parameters'],{'max_tokens':2048,'enable_thinking':False})
            # Official RAGAS PydanticPrompt schema and fixture responses.
            if 'StatementGeneratorOutput' in prompt or 'Break down each sentence' in prompt:
                return {'output_tokens':10},json.dumps({'statements':['Supported answer']})
            return {'output_tokens':10},json.dumps({'statements':[{'statement':'Supported answer','verdict':1,'reason':'Supported by retrieved source'}]})
        # Target explicitly supplies retrieval: never inherit imported retrieval.
        target={'actual_output':'Supported answer','retrieval_context':['Supported source'],'retrieved_chunk_ids':['chunk'],'duration_ms':1,'metadata':{}}
        with patch('backend.evaluation_execution.call_target',return_value=target),patch('backend.evaluation_execution.stream_chat',side_effect=model):
            run=self.execute(self.prepare())
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['metric_results'][0]['score'],1)
        self.assertIn('Supported by retrieved source',run['metric_results'][0]['reason'])

    def test_retrieval_only_records_real_ids_and_no_judge_calls(self):
        self.request.update(template='retrieve_only',metrics=[])
        self.request['target']['benchmark']='dify-retrieve'
        self.config['dataset_id']='external-kb'
        response=unittest.mock.MagicMock(status_code=200,content=b'{}')
        response.json.return_value={'records':[{'segment':{'id':'actual-chunk','document_id':'doc','content':'Actual content'}}]}
        with patch('backend.evaluation_execution.requests.get') as get, \
             patch('backend.evaluation_execution.requests.post',return_value=response) as post:
            run=self.execute(self.prepare())
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual(run['cases'][0]['observation']['retrieved_chunk_ids'],['actual-chunk'])
        self.assertEqual(run['cases'][0]['observation']['retrieval_context'],['Actual content'])
        self.assertEqual(post.call_count,1)
        get.assert_not_called()
        self.assertEqual(post.call_args.kwargs['json'], {'query': 'Question'})
        self.assertNotIn('retrieval_model', run['cases'][0]['observation']['metadata'])
        self.assertEqual(run['usage']['target']['requests'], 1)
        self.assertEqual(summarize(run)['metrics'],{})

    def test_dify_retrieval_sends_only_query_for_every_sample(self):
        self.request.update(template='retrieve_only',metrics=[])
        self.request['target'].update(benchmark='dify-retrieve', retrieval_top_k=7)
        self.config['dataset_id']='external kb/1'
        self.repo.add(self.dataset_id, [{'input':'Second question','expected_output':'Reference'}])
        response=unittest.mock.MagicMock(status_code=200,content=b'{}')
        response.json.return_value={'records':[]}
        with patch('backend.evaluation_execution.requests.get') as get, \
             patch('backend.evaluation_execution.requests.post',return_value=response) as post:
            run=self.execute(self.prepare())
        self.assertEqual(run['status'],'completed',run)
        get.assert_not_called()
        self.assertEqual(post.call_count,2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['json'], {'query': 'Question'})
        self.assertEqual(run['usage']['target']['requests'],2)

    def test_dify_retrieval_request_counts_against_budget(self):
        self.request.update(template='retrieve_only',metrics=[])
        self.request['target']['benchmark']='dify-retrieve'
        self.request['budget']['max_requests']=1
        self.config['dataset_id']='external-kb'
        with patch('backend.evaluation_execution.requests.get') as get, \
             patch('backend.evaluation_execution.requests.post') as post:
            run=self.execute(self.prepare())
        get.assert_not_called();post.assert_called_once()
        self.assertEqual(run['error'],'budget_exhausted')
        self.assertEqual(run['usage']['target']['requests'],1)

    def test_dify_missing_settings_and_rejected_request_have_safe_permanent_errors(self):
        self.request.update(template='retrieve_only',metrics=[])
        self.request['target']['benchmark']='dify-retrieve'
        self.config['dataset_id']='external-kb'
        for kind in ['missing','rejected','unauthorized']:
            self.request['idempotency_key']=kind
            settings=dify_settings_response()
            response=unittest.mock.MagicMock(status_code=400,content=b'remote-secret')
            if kind=='missing':settings.json.return_value={}
            if kind=='unauthorized':
                import requests
                settings.status_code=403
                settings.raise_for_status.side_effect=requests.HTTPError('remote-secret',response=settings)
            with patch('backend.evaluation_execution.requests.get',return_value=settings) as get, \
                 patch('backend.evaluation_execution.requests.post',return_value=response) as post:
                run=self.execute(self.prepare())
            expected={'missing':'dify_retrieval_configuration_missing', 'rejected':'dify_retrieval_rejected',
                      'unauthorized':'authentication_failed'}[kind]
            self.assertEqual(run['error'],expected,run)
            self.assertTrue(all(case['error']==expected for case in run['cases']))
            self.assertEqual(get.call_count,1)
            self.assertEqual(post.call_count,int(kind=='rejected'))
            self.assertNotIn('remote-secret',json.dumps(run))

    def test_parallel_claim_and_expired_lease_fences_old_worker(self):
        run=self.prepare();self.evaluations.enqueue(run['id'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims=list(pool.map(lambda i:self.evaluations.claim(str(i)),range(4)))
        self.assertEqual(sum(v is not None for v in claims),1)
        old=next(c for c in claims if c)
        with self.store._session() as c:
            c.execute("UPDATE evaluation_runs SET payload=(payload::jsonb || '{\"lease_until\":\"2000-01-01T00:00:00Z\"}'::jsonb)::text WHERE id=%s",(run['id'],))
        claim=self.evaluations.claim('replacement')
        self.assertNotEqual(claim['worker_token'],old['worker_token'])
        with self.assertRaises(Exception):
            self.evaluations.reserve_request(run['id'],old['worker_token'],'target',10)

    def test_budget_stops_without_false_scores_and_changed_config_refused(self):
        self.request['budget']['max_requests']=1
        with patch('backend.evaluation_execution.stream_chat',return_value=({'output_tokens':2},'answer')) as model:
            run=self.execute(self.prepare())
        self.assertEqual(run['error'],'budget_exhausted',run)
        self.assertEqual(model.call_count,1)
        self.assertTrue(all(m['score'] is None for m in run['metric_results']))
        self.request['idempotency_key']='changed-config'
        prepared=self.prepare();self.config['model']='changed'
        with patch('backend.evaluation_execution.stream_chat') as model:
            run=self.execute(prepared)
        self.assertEqual(run['error'],'configuration_changed')
        model.assert_not_called()

    def test_deterministic_ranking_requires_annotations_and_empty_is_zero(self):
        annotation={'annotations':{'relevant_chunk_ids':['b','c'],'graded_relevance':{'b':2,'c':1}}}
        for metric_id,expected in [('rules.recall_at_k',.5),('rules.mrr',.5)]:
            score,_=score_rule({'id':metric_id,'k':2},annotation,{'retrieved_chunk_ids':['a','b']})
            self.assertEqual(score,expected)
        score,_=score_rule({'id':'rules.ndcg','k':2},annotation,{'retrieved_chunk_ids':['b','c']})
        self.assertEqual(score,1)
        self.assertEqual(score_rule({'id':'rules.recall_at_k','k':2},annotation,{'retrieved_chunk_ids':[]})[0],0)

    def test_cache_reuses_only_identical_content_and_judge_configuration(self):
        self.request['metrics']=[{'id':'deepeval.answer_relevancy'}]
        prepared=self.prepare();version=prepared['evidence']['dataset_version_id']
        target={'actual_output':'answer','retrieval_context':None,'duration_ms':1,'metadata':{}}
        with patch('backend.evaluation_execution.call_target',return_value=target),patch('backend.evaluation_execution.score_metric',return_value=(.8,'fixture verdict')) as judge:
            first=self.execute(prepared)
            self.request.update(dataset_version_id=version,idempotency_key='cached')
            second=self.execute(self.prepare())
        self.assertEqual(judge.call_count,1)
        self.assertTrue(second['metric_results'][0]['usage']['cache_hit'])
        self.request.update(idempotency_key='new-rule',judge_prompt='Different rule')
        with patch('backend.evaluation_execution.call_target',return_value=target),patch('backend.evaluation_execution.score_metric',return_value=(.3,'changed')) as judge:
            third=self.execute(self.prepare())
        self.assertEqual(judge.call_count,1);self.assertEqual(third['metric_results'][0]['score'],.3)
        self.assertFalse(third['metric_results'][0]['usage']['cache_hit'])

    def test_permanent_errors_no_retry_transient_bounded_and_cancel_stops(self):
        import requests
        self.request['metrics']=[{'id':'rules.required_text','required_text':['answer']}]
        self.request['scoring_engine']='rules'
        self.request['budget'].update(max_retries=2)
        error=requests.HTTPError(response=unittest.mock.MagicMock(status_code=401))
        with patch('backend.evaluation_execution.stream_chat',side_effect=error) as transport:
            run=self.execute(self.prepare())
        self.assertEqual(transport.call_count,1);self.assertEqual(run['error'],'authentication_failed')
        self.request['idempotency_key']='transient'
        with patch('backend.evaluation_execution.stream_chat',side_effect=requests.Timeout) as transport:
            run=self.execute(self.prepare())
        self.assertEqual(transport.call_count,3);self.assertEqual(run['status'],'failed')
        self.request['idempotency_key']='cancel'
        prepared=self.prepare();self.evaluations.enqueue(prepared['id']);claim=self.evaluations.claim('cancel-worker')
        self.evaluations.cancel(prepared['id'])
        with patch('backend.evaluation_execution.stream_chat') as transport:
            run_evaluation(self.evaluations,claim,self.resolve)
        transport.assert_not_called()
        self.assertEqual(self.evaluations.get(prepared['id'])['status'],'cancelled')

    def test_resume_completed_target_after_worker_death_and_no_duplicate_commit(self):
        self.request['scoring_engine']='rules';self.request['metrics']=[{'id':'rules.required_text','required_text':['answer']}]
        prepared=self.prepare();self.evaluations.enqueue(prepared['id']);old=self.evaluations.claim('dead')
        self.evaluations.record_target(prepared['id'],prepared['cases'][0]['id'],old['worker_token'],observation={'actual_output':'answer','duration_ms':5})
        with self.store._session() as c:
            c.execute("UPDATE evaluation_runs SET payload=(payload::jsonb || '{\"lease_until\":\"2000-01-01T00:00:00Z\"}'::jsonb)::text WHERE id=%s",(prepared['id'],))
        claim=self.evaluations.claim('replacement')
        with patch('backend.evaluation_execution.call_target') as target:
            run_evaluation(self.evaluations,claim,self.resolve)
        target.assert_not_called()
        run=self.evaluations.get(prepared['id']);self.assertEqual(run['status'],'completed')
        self.assertEqual(len(run['metric_results']),1)
        self.assertIsNone(self.evaluations.claim('duplicate'))

    def test_atomic_budget_reservation_under_competing_threads(self):
        self.request['budget']['max_requests']=3
        run=self.prepare();self.evaluations.enqueue(run['id']);claim=self.evaluations.claim('concurrent-sdk')
        def reserve(_index):
            try:self.evaluations.reserve_request(run['id'],claim['worker_token'],'judge',256);return True
            except Exception as error:
                self.assertEqual(error.status_code,402);return False
        with ThreadPoolExecutor(max_workers=8) as pool:accepted=list(pool.map(reserve,range(12)))
        self.assertEqual(sum(accepted),3)
        self.assertEqual(self.evaluations.get(run['id'])['usage']['judge']['requests'],3)
