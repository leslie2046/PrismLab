"""Dataset lists use the latest immutable evaluation and real per-metric scores."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from backend.evaluation_store import EvaluationStore
from tests.database import create_test_store
from tests import test_evaluations as evaluations_fixture
from tests import test_datasets as datasets_fixture


class DatasetEvaluationSummaryTests(unittest.TestCase):
    setUp = evaluations_fixture.EvaluationTests.setUp
    prepare = evaluations_fixture.EvaluationTests.prepare

    def test_no_evaluation_and_empty_page(self):
        response = self.client.get('/api/datasets')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['items'][0]['latest_evaluation'])
        self.assertEqual(self.client.get('/api/datasets?q=not-found').json()['items'], [])
        store = MagicMock()
        self.assertEqual(EvaluationStore(store).latest_summaries([]), {})
        store._session.assert_not_called()

    def test_scores_match_detail_zero_counts_and_missing_results(self):
        self.repo.add(self.dataset_id, [
            {'input': 'No reference', 'context': ['Source']},
            {'input': 'Third question', 'expected_output': 'Answer', 'context': ['Source']},
        ])
        run = self.prepare()
        token = self.evaluations.start(run['id'])
        for case in run['cases']:
            self.evaluations.record_target(run['id'], case['id'], token,
                                           observation={'actual_output': 'Answer', 'retrieval_context': ['Source']})
        by_metric = {}
        for result in run['metric_results']:
            if result['status'] == 'missing_data':
                continue
            index = by_metric.get(result['metric_id'], 0)
            score = 0 if index == 0 else .8
            by_metric[result['metric_id']] = index + 1
            self.evaluations.record_metric(run['id'], result['id'], token, score=score)
        detail = self.client.get('/api/evaluation-runs/' + run['id']).json()
        listing = self.client.get('/api/datasets').json()['items'][0]['latest_evaluation']
        self.assertEqual(listing['metrics'], detail['summary']['metrics'])
        self.assertEqual(listing['metrics']['deepeval.reference_correctness']['scored'], 2)
        self.assertAlmostEqual(listing['metrics']['deepeval.reference_correctness']['mean'], .4)
        self.assertEqual(listing['case_count'], 3)
        self.assertEqual(listing['status'], 'partial_failed')
        for private in ('worker_token', 'evidence', 'cases', 'metric_results'):
            self.assertNotIn(private, listing)

    def test_latest_created_run_including_reused_version_and_unscored_metrics(self):
        old = self.prepare()
        newer = self.prepare({**self.request, 'dataset_id': None,
                              'dataset_version_id': old['evidence']['dataset_version_id'],
                              'idempotency_key': 'newer', 'name': 'Newest',
                              'metrics': [{'id': 'deepeval.faithfulness'}]})
        # A late update on the old run must not change which evaluation is latest.
        with self.store._session() as connection:
            self.evaluations._save_run(connection, old)
        summary = self.client.get('/api/datasets').json()['items'][0]['latest_evaluation']
        self.assertEqual(summary['id'], newer['id'])
        self.assertEqual(summary['name'], 'Newest')
        self.assertEqual(summary['status'], 'prepared')
        self.assertEqual(list(summary['metrics']), ['deepeval.faithfulness'])
        self.assertIsNone(summary['metrics']['deepeval.faithfulness']['mean'])
        self.assertEqual(summary['metrics']['deepeval.faithfulness']['scored'], 0)
        self.evaluations.delete(newer['id'])
        self.assertEqual(self.client.get('/api/datasets').json()['items'][0]['latest_evaluation']['id'], old['id'])

    def test_page_filter_dataset_and_workspace_boundaries(self):
        run = self.prepare()
        other_id = datasets_fixture.DatasetApiTests.create(self)
        self.repo.add(other_id, [{'input': 'Other', 'expected_output': 'Answer'}])
        other_run = self.prepare({**self.request, 'dataset_id': other_id, 'idempotency_key': 'other'})
        self.client.put('/api/datasets/' + self.dataset_id, json={'name': 'Find this dataset'})
        self.client.put('/api/datasets/' + other_id, json={'name': 'Other dataset'})
        page = self.client.get('/api/datasets?q=Find&page_size=1').json()
        self.assertEqual(page['total'], 1)
        self.assertEqual(page['items'][0]['latest_evaluation']['id'], run['id'])
        summaries = self.evaluations.latest_summaries([other_id])
        self.assertEqual(list(summaries), [other_id])
        self.assertEqual(summaries[other_id]['id'], other_run['id'])
        with tempfile.TemporaryDirectory() as directory:
            other_store = create_test_store(self, Path(directory))
            self.assertEqual(EvaluationStore(other_store).latest_summaries([self.dataset_id]), {})


if __name__ == '__main__':
    unittest.main()
