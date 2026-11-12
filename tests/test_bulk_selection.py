import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from backend import main as web_main
from backend.store import utc_now
from tests import test_datasets as dataset_fixtures
from tests import test_workspaces as workspace_fixtures


class DatasetBulkTests(unittest.TestCase):
    setUp = dataset_fixtures.DatasetApiTests.setUp
    create = dataset_fixtures.DatasetApiTests.create

    def test_complete_filtered_snapshot_and_atomic_status_and_delete(self):
        dataset = self.create()
        self.repo.add(dataset, [{'input': f'Keep {i}', 'additional_metadata': {'n': i}} for i in range(112)], origin='generated')
        self.repo.add(dataset, [{'input': 'Other'}], origin='import')
        path = f'/api/datasets/{dataset}/items'
        snapshot = self.client.get(path + '/selection?q=Keep').json()
        self.assertEqual(len(snapshot['ids']), 112)
        self.assertEqual(self.client.get(path + '?q=Keep&page_size=10').json()['total'], 112)
        # Snapshot excludes records that arrive later.
        self.repo.add(dataset, [{'input': 'Keep later'}])
        response = self.client.post(path + '/bulk', json={'action': 'archive', 'ids': snapshot['ids'] * 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['succeeded']), 112)
        rows = self.repo.items(dataset, size=200)['items']
        archived = [row for row in rows if row['status'] == 'archived']
        self.assertEqual(len(archived), 112)
        self.assertTrue(all(row['origin'] == 'manual' for row in archived))
        self.assertEqual({row['additional_metadata']['n'] for row in archived}, set(range(112)))
        response = self.client.post(path + '/bulk', json={'action': 'archive', 'ids': snapshot['ids']})
        self.assertEqual(len(response.json()['skipped']), 112)
        response = self.client.post(path + '/bulk', json={'action': 'enable', 'ids': snapshot['ids']})
        self.assertEqual(len(response.json()['succeeded']), 112)
        response = self.client.post(path + '/bulk', json={'action': 'delete', 'ids': snapshot['ids']})
        self.assertEqual(len(response.json()['succeeded']), 112)
        self.assertEqual(self.repo.get(dataset)['item_count'], 2)
        self.assertEqual({row['input'] for row in self.repo.items(dataset)['items']}, {'Other', 'Keep later'})

    def test_foreign_missing_ids_validation_and_generation_lock(self):
        first, second = self.create(), self.create()
        self.repo.add(first, [{'input': 'First'}]); self.repo.add(second, [{'input': 'Second'}])
        one = self.repo.items(first)['items'][0]['id']; other = self.repo.items(second)['items'][0]['id']
        path = f'/api/datasets/{first}/items'
        for foreign in [other, 'missing']:
            response = self.client.post(path + '/bulk', json={'action': 'delete', 'ids': [one, foreign]})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.repo.get(first)['item_count'], 1)
        for payload in [{'action': 'delete', 'ids': []}, {'action': 'unknown', 'ids': [one]}, {'action': 'enable', 'ids': [one], 'all': True}]:
            self.assertEqual(self.client.post(path + '/bulk', json=payload).status_code, 422)
        self.repo.mutate(first, lambda c, d: d.update(status='generating'))
        self.assertEqual(self.client.post(path + '/bulk', json={'action': 'delete', 'ids': [one]}).status_code, 409)
        self.assertEqual(self.client.get(path + '/selection').json()['ids'], [one])
        self.assertEqual(self.client.get(path + '/selection?q=%').json()['total'], 0)


class WorkspaceBulkTests(unittest.TestCase):
    setUp = workspace_fixtures.WorkspaceTests.setUp
    drop_workspace_schemas = workspace_fixtures.WorkspaceTests.drop_workspace_schemas
    client = workspace_fixtures.WorkspaceTests.client
    create_team = workspace_fixtures.WorkspaceTests.create_team
    invite = workspace_fixtures.WorkspaceTests.invite
    join = workspace_fixtures.WorkspaceTests.join

    def target(self):
        return self.manager.runtime(self.owner_status['personal_workspace_id']).store

    def plan(self, identifier, status='paused', **extra):
        now = utc_now()
        self.target().put_plan({'id': identifier, 'name': 'Needle '+identifier, 'benchmark': 'embedding', 'status': status,
                                'created_at': now, 'updated_at': now, 'start_at': (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), **extra})

    def record_run(self, identifier, status='completed'):
        now = utc_now()
        self.target().create({'id': identifier, 'name': 'Needle '+identifier, 'status': status, 'benchmark': 'embedding',
                              'created_at': now, 'updated_at': now, 'scenarios': []})

    def test_plan_full_selection_filters_and_partial_controls(self):
        for i in range(115): self.plan('plan'+str(i), repeat_mode='count')
        self.plan('outside', benchmark='llm')
        snapshot = self.owner.get('/api/plans/selection?q=Needle&benchmark=embedding&status=paused&repeat_mode=count').json()
        self.assertEqual(snapshot['total'], 115)
        self.assertEqual(self.owner.get('/api/plans?page=1&page_size=10&benchmark=embedding').json()['total'], 115)
        self.plan('later')
        response = self.owner.post('/api/plans/bulk', json={'action': 'start', 'ids': snapshot['ids'] + ['missing', snapshot['ids'][0]]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['succeeded']), 115)
        self.assertEqual(response.json()['failed'][0]['id'], 'missing')
        self.assertEqual(self.target().get_plan('later')['status'], 'paused')
        self.assertEqual(self.owner.post('/api/plans/bulk', json={'action': 'start', 'ids': snapshot['ids']}).json()['skipped'], snapshot['ids'])
        self.assertEqual(len(self.owner.post('/api/plans/bulk', json={'action': 'pause', 'ids': snapshot['ids']}).json()['succeeded']), 115)
        self.assertEqual(len(self.owner.post('/api/plans/bulk', json={'action': 'stop', 'ids': snapshot['ids']}).json()['succeeded']), 115)
        self.record_run('aaaa00000001')
        self.assertEqual(len(self.owner.post('/api/plans/bulk', json={'action': 'delete', 'ids': snapshot['ids']}).json()['succeeded']), 115)
        self.assertIsNotNone(self.target().get('aaaa00000001'))

    def test_runs_snapshot_terminal_guards_cancel_and_report_cleanup(self):
        self.record_run('aaaa00000002'); self.record_run('aaaa00000003', 'running')
        self.target().begin_ai_report('aaaa00000002', 'generation', {'model': 'm'})
        self.target().finish_ai_report('aaaa00000002', 'generation', 'report')
        snapshot = self.owner.get('/api/runs/selection?status=completed&benchmark=embedding&q=Needle').json()
        self.assertEqual(snapshot['ids'], ['aaaa00000002'])
        response = self.owner.post('/api/runs/bulk', json={'action': 'delete', 'ids': ['aaaa00000002','aaaa00000003','missing']}).json()
        self.assertEqual(response['succeeded'], ['aaaa00000002'])
        self.assertEqual(response['skipped'], ['aaaa00000003'])
        self.assertEqual(response['failed'][0]['id'], 'missing')
        self.assertEqual(self.target().ai_report('aaaa00000002')['status'], 'none')
        with patch.object(web_main, 'cancel_run', new=AsyncMock(return_value={})) as cancel:
            response = self.owner.post('/api/runs/bulk', json={'action': 'cancel', 'ids': ['aaaa00000003']})
            self.assertEqual(response.json()['succeeded'], ['aaaa00000003'])
            cancel.assert_awaited_once_with('aaaa00000003')

    def test_workspace_isolation_and_member_delete_permissions(self):
        self.plan('private'); self.record_run('aaaa00000004')
        self.assertEqual(self.outsider.get('/api/plans/selection').json()['ids'], [])
        self.assertEqual(self.outsider.get('/api/runs/selection').json()['ids'], [])
        self.assertEqual(self.outsider.post('/api/plans/bulk', json={'action':'delete','ids':['private']}).json()['succeeded'], [])
        self.assertEqual(self.outsider.post('/api/runs/bulk', json={'action':'delete','ids':['aaaa00000004']}).json()['succeeded'], [])
        team = self.create_team(); self.join(team); self.owner.headers['X-Workspace-Id'] = team
        target = self.manager.runtime(team).store; now = utc_now()
        target.put_plan({'id':'team-plan','name':'Team','status':'active','created_at':now,'updated_at':now})
        target.create({'id':'aaaa00000005','name':'Team','status':'completed','created_at':now,'updated_at':now,'scenarios':[]})
        for resource, identifier in [('plans','team-plan'),('runs','aaaa00000005')]:
            response=self.member.post('/api/'+resource+'/bulk',json={'action':'delete','ids':[identifier]})
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.member.post('/api/plans/bulk',json={'action':'pause','ids':['team-plan']}).json()['succeeded'],['team-plan'])
        self.assertEqual(self.owner.post('/api/plans/bulk',json={'action':'delete','ids':['team-plan']}).json()['succeeded'],['team-plan'])
        from backend.dataset_routes import install_dataset_routes
        install_dataset_routes(self.app, web_main.store, web_main._playground_access, web_main._dataset_concrete_store)
        created_response=self.member.post('/api/datasets',json={'name':'Team data'})
        self.assertEqual(created_response.status_code,201,created_response.text)
        created=created_response.json()['id']
        self.member.post(f'/api/datasets/{created}/items',json={'input':'Team question'})
        ids=self.member.get(f'/api/datasets/{created}/items/selection').json()['ids']
        self.assertEqual(self.member.post(f'/api/datasets/{created}/items/bulk',json={'action':'archive','ids':ids}).status_code,200)
        exported = self.member.post(f'/api/datasets/{created}/export', json={'ids': ids})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertEqual([row['input'] for row in exported.json()], ['Team question'])
        self.assertEqual(self.outsider.post(f'/api/datasets/{created}/export', json={'ids': ids}).status_code, 404)
        self.assertEqual(self.outsider.get(f'/api/datasets/{created}/items/selection').status_code,404)
        self.assertEqual(self.outsider.post(f'/api/datasets/{created}/items/bulk',json={'action':'delete','ids':ids}).status_code,404)

    def test_invalid_selection_requests_do_not_mutate(self):
        self.plan('safe')
        for payload in [{'action':'delete','ids':[]}, {'action':'delete','ids':['safe'],'filter':{}}, {'action':'unknown','ids':['safe']}]:
            self.assertEqual(self.owner.post('/api/plans/bulk',json=payload).status_code,422)
        self.assertIsNotNone(self.target().get_plan('safe'))
        self.assertEqual(self.owner.get('/api/plans/selection?status=invalid').status_code,422)
        self.assertEqual(self.owner.get('/api/runs/selection?q=%').json()['total'],0)
