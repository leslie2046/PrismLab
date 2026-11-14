"""Explicit evaluation modes, dataset purposes and retrieval scoring boundaries."""
import json
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

from backend.dataset_annotations import item_hash
from backend.evaluation_contracts import evaluation_type
from backend.evaluation_results import comparison_compatible
from tests import test_evaluations as fixtures
from tests import test_evaluation_execution as execution_fixtures


class EvaluationTypeTests(unittest.TestCase):
    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        cutoff = patch('backend.evaluation_recall.read_dify_top_k', return_value=2)
        cutoff.start()
        self.addCleanup(cutoff.stop)
    prepare = fixtures.EvaluationTests.prepare
    execute = execution_fixtures.ExecutionTests.execute

    def retrieval_request(self):
        self.request.update(evaluation_type='retrieval', template='retrieval', scoring_engine='rules',
                            judge=None, metrics=[{'id':'rules.recall_at_k','k':2}, {'id':'rules.mrr','k':2}, {'id':'rules.ndcg','k':2}])
        self.request['target'].update(benchmark='dify-retrieve', retrieval_top_k=2)
        self.config['dataset_id']='kb'

    def test_mode_rejects_incompatible_targets_metrics_and_empty_scoring(self):
        self.retrieval_request()
        for changes in [dict(metrics=[]), dict(metrics=[{'id':'deepeval.faithfulness'}]),
                        dict(sample_kinds=['conversation']), dict(template='retrieve_only'),
                        dict(target={**self.request['target'],'benchmark':'llm'}),
                        dict(evaluation_type='rag')]:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/api/evaluation-runs',json={**self.request,**changes}).status_code,422)
        self.request['evaluation_type']='rag'; self.request['template']='reference_qa'
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,422)
        self.request.update(evaluation_type='retrieval',template='retrieval',metrics=[{'id':'ragas.context_recall'}],scoring_engine='ragas',judge=self.selection)
        self.assertEqual(self.prepare()['evidence']['evaluation_type'],'retrieval')

    def test_purpose_roundtrip_filter_import_and_legacy_update(self):
        for purpose in ['rag','retrieval','both']:
            response=self.client.post('/api/datasets',json={'name':purpose,'evaluation_purpose':purpose})
            self.assertEqual(response.status_code,201,response.text)
            ds=response.json(); self.assertEqual(ds['evaluation_purpose'],purpose)
            updated=self.client.put('/api/datasets/'+ds['id'],json={'name':'renamed'})
            self.assertEqual(updated.json()['evaluation_purpose'],purpose)
            listing=self.client.get('/api/datasets',params={'purpose':purpose,'page_size':1}).json()
            self.assertEqual(len(listing['items']),1)
            self.assertEqual(listing['items'][0]['evaluation_purpose'],purpose)
        imported=self.client.post('/api/datasets/import',data={'name':'import','evaluation_purpose':'retrieval'},files={'file':('test.json',b'[{"input":"q"}]')})
        self.assertEqual(imported.status_code,201,imported.text)
        self.assertEqual(imported.json()['evaluation_purpose'],'retrieval')
        self.assertEqual(self.client.post('/api/datasets',json={'name':'invalid','evaluation_purpose':'unknown'}).status_code,422)
        with self.store._session() as c:
            c.execute("UPDATE datasets SET payload=(payload::jsonb - 'evaluation_purpose')::text WHERE id=%s",(self.dataset_id,))
        self.assertEqual(self.repo.get(self.dataset_id)['evaluation_purpose'],'both')
        self.assertIn(self.dataset_id,[d['id'] for d in self.client.get('/api/datasets?purpose=both').json()['items']])

    def test_generation_preserves_purpose_and_missing_labels_are_not_scores(self):
        with patch('backend.dataset_routes.build_dataset') as build:
            response=self.client.post('/api/datasets/generate',data={'name':'generated','evaluation_purpose':'retrieval',
                'options':json.dumps({'size':1,'llm':self.selection})},files={'files':('source.txt',b'Source text')})
        self.assertEqual(response.status_code,202,response.text)
        self.assertEqual(response.json()['evaluation_purpose'],'retrieval')
        build.assert_called_once()
        self.retrieval_request()
        target=MagicMock(status_code=200,content=b'{}');target.json.return_value={'records':[]}
        with patch('backend.evaluation_execution.requests.get'), \
             patch('backend.evaluation_execution.requests.post',return_value=target),patch('backend.evaluation_execution.stream_chat') as model:
            run=self.execute(self.prepare())
        self.assertTrue(all(metric['status']=='missing_data' and metric['score'] is None for metric in run['metric_results']))
        model.assert_not_called()

    def test_frozen_purpose_is_authoritative_and_edits_preserve_version(self):
        self.repo.mutate(self.dataset_id,lambda c,d:d.update(evaluation_purpose='rag'))
        rag=self.prepare({**self.request,'evaluation_type':'rag'})
        frozen=self.evaluations.version(rag['evidence']['dataset_version_id'])
        self.retrieval_request();self.request['idempotency_key']='retrieval'
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,400)
        self.repo.mutate(self.dataset_id,lambda c,d:d.update(evaluation_purpose='both'))
        self.request['dataset_version_id']=frozen['id']
        self.assertEqual(self.client.post('/api/evaluation-runs',json=self.request).status_code,400)
        self.request.pop('dataset_version_id')
        self.assertEqual(self.prepare()['evidence']['evaluation_type'],'retrieval')
        self.assertEqual(self.evaluations.version(frozen['id']),frozen)

    def test_retrieval_scores_real_ids_and_never_generates_answer(self):
        self.retrieval_request()
        item=self.repo.items(self.dataset_id)['items'][0]
        annotation={'content_hash':item_hash(item),'review_status':'approved','relevant_chunk_ids':['a'], 'graded_relevance':{'a':3,'b':0}}
        with self.store._session() as c:
            c.execute('INSERT INTO dataset_annotations VALUES (%s,%s,%s)',(item['id'],self.dataset_id,json.dumps(annotation)))
        response=MagicMock(status_code=200,content=b'{}')
        response.json.return_value={'records':[{'segment':{'id':'b','content':'irrelevant'}},{'segment':{'id':'a','content':'relevant'}}]}
        with patch('backend.evaluation_execution.requests.get'), \
             patch('backend.evaluation_execution.requests.post',return_value=response) as post,patch('backend.evaluation_execution.stream_chat') as model:
            run=self.execute(self.prepare())
        self.assertEqual(run['status'],'completed',run)
        self.assertEqual([r['score'] for r in run['metric_results'] if r['metric_id']=='rules.recall_at_k'],[1])
        self.assertEqual([r['score'] for r in run['metric_results'] if r['metric_id']=='rules.mrr'],[.5])
        self.assertIsNone(run['cases'][0]['observation']['actual_output'])
        model.assert_not_called(); self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.kwargs['json'], {'query': 'Question'})
        self.assertIsNone(run['evidence']['result_judge'])

    def test_list_returns_typed_summaries_without_detail_or_history_rewrites(self):
        rag=self.prepare({**self.request,'evaluation_type':'rag'})
        self.retrieval_request();self.request['idempotency_key']='retrieval-summary'
        retrieval=self.prepare()
        with self.store._session() as c:
            row=c.execute('SELECT * FROM evaluation_runs WHERE id=%s',(retrieval['id'],)).fetchone()
            evidence=json.loads(row['evidence']);evidence.pop('evaluation_type')
            payload=json.loads(row['payload']);payload['id']='legacy-summary'
            c.execute('INSERT INTO evaluation_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (payload['id'],row['created_at'],row['updated_at'],row['status'],'legacy-summary-key',row['request_hash'],json.dumps(evidence),json.dumps(payload)))
            before=c.execute('SELECT id,evidence,payload FROM evaluation_runs ORDER BY id').fetchall()
        response=self.client.get('/api/evaluation-runs?limit=100')
        self.assertEqual(response.status_code,200,response.text)
        summaries=response.json()['items'];self.assertEqual(len(summaries),3)
        types={r['id']:r['evaluation_type'] for r in summaries}
        self.assertEqual(types,{rag['id']:'rag',retrieval['id']:'retrieval','legacy-summary':'retrieval'})
        for summary in summaries:
            for key in ('evidence','cases','metric_results'):
                self.assertNotIn(key,summary)
        self.assertIn('evidence',self.client.get('/api/evaluation-runs/'+rag['id']).json())
        with self.store._session() as c:
            self.assertEqual(c.execute('SELECT id,evidence,payload FROM evaluation_runs ORDER BY id').fetchall(),before)

    def test_legacy_classification_and_comparison_types(self):
        run=self.prepare(); old=deepcopy(run)
        old['evidence'].pop('evaluation_type')
        self.assertEqual(evaluation_type(old['evidence']),'rag')
        self.assertTrue(comparison_compatible(run,old))
        old['evidence']['evaluation_type']='retrieval'
        self.assertFalse(comparison_compatible(run,old))
        self.assertEqual(evaluation_type({'target':{'benchmark':'dify-retrieve'}}),'retrieval')
