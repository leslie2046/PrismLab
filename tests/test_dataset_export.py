import unittest

from backend.dataset_golden import golden_dict
from backend.datasets import parse_import
from tests import test_datasets as fixtures


class DatasetExportTests(unittest.TestCase):
    setUp = fixtures.DatasetApiTests.setUp
    create = fixtures.DatasetApiTests.create

    def test_selected_export_in_all_formats_preserves_goldens_and_order(self):
        dataset = self.create()
        self.repo.add(dataset, [{
            'input': f'问题 {index}', 'expected_output': f'答案 {index}',
            'context': ['第一段\n第二行', '另一段'],
            'additional_metadata': {'index': index, 'tags': ['评测']},
            'status': 'archived' if index == 12 else 'active',
        } for index in range(25)])
        rows = self.repo.items(dataset, size=100)['items']
        chosen = [rows[0], rows[12], rows[24]]
        # Export follows dataset order even when clicks cross pages in reverse order.
        ids = [row['id'] for row in reversed(chosen)]
        expected = [golden_dict(row) for row in chosen]
        before = self.repo.get(dataset)
        for format in ['json', 'jsonl', 'csv']:
            response = self.client.post(f'/api/datasets/{dataset}/export?format={format}', json={'ids': ids + ids})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(f'dataset-{dataset}.{format}', response.headers['content-disposition'])
            imported = parse_import('export.' + format, response.content)
            self.assertEqual([golden_dict(row) for row in imported], expected)
            self.assertTrue(all('id' not in row and 'status' not in row and 'origin' not in row for row in expected))
            # Existing full dataset export is retained, including archived records.
            full = self.client.get(f'/api/datasets/{dataset}/export?format={format}')
            self.assertEqual(full.status_code, 200)
            self.assertEqual(len(parse_import('export.' + format, full.content)), 25)
        self.assertEqual(self.repo.get(dataset), before)
        self.assertEqual(self.repo.items(dataset, size=100)['items'], rows)

    def test_invalid_and_stale_selections_never_export_other_records(self):
        first, second = self.create(), self.create()
        self.repo.add(first, [{'input': 'First'}])
        self.repo.add(second, [{'input': 'Private second'}])
        one = self.repo.items(first)['items'][0]['id']
        other = self.repo.items(second)['items'][0]['id']
        path = f'/api/datasets/{first}/export'
        for ids in [[one, other], [one, 'missing']]:
            response = self.client.post(path, json={'ids': ids})
            self.assertEqual(response.status_code, 404)
            self.assertNotIn('Private second', response.text)
        for payload in [{'ids': []}, {'ids': [one], 'all': True}, {'ids': ['x' * 129]}, {'ids': [one] * 20001}]:
            self.assertEqual(self.client.post(path, json=payload).status_code, 422)
        self.assertEqual(self.client.post(path + '?format=xml', json={'ids': [one]}).status_code, 422)
        self.assertEqual(self.client.post('/api/datasets/missing/export', json={'ids': [one]}).status_code, 404)
        # Export is read only and remains available during generation.
        self.repo.mutate(first, lambda c, d: d.update(status='generating'))
        self.assertEqual(self.client.post(path, json={'ids': [one]}).status_code, 200)

    def test_empty_full_export_remains_valid(self):
        dataset = self.create()
        for format in ['json', 'jsonl', 'csv']:
            response = self.client.get(f'/api/datasets/{dataset}/export?format={format}')
            self.assertEqual(response.status_code, 200)
            if format == 'json':
                self.assertEqual(response.json(), [])
            elif format == 'jsonl':
                self.assertEqual(response.content, b'')
            else:
                self.assertIn('input', response.text)
