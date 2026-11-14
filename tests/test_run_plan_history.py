"""Run history groups by immutable plan identity before applying page limits."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import main as web
from tests.database import create_test_store
from tests import test_workspaces as workspace_fixtures


def run(identifier, plan_id, stamp, *, name='Repeated name', status='completed'):
    return {'id':identifier,'plan_id':plan_id,'name':name,'benchmark':'reranker','status':status,
            'created_at':stamp,'updated_at':stamp,'scenarios':[], 'completed_scenarios':1,'total_scenarios':1}


class RunPlanHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.store=create_test_store(self,Path(temporary.name))
        replacement=patch.object(web,'store',self.store);replacement.start();self.addCleanup(replacement.stop)
        app=FastAPI()
        app.router.routes.extend(route for route in web.app.router.routes if getattr(route,'path','') in
                                 {'/api/run-plan-groups','/api/runs','/api/runs/{run_id}'})
        self.client=TestClient(app);self.addCleanup(self.client.close)

    def add(self, identifier, plan_id, i, **extra):
        value=run(identifier,plan_id,f'2026-11-13T{i//3600:02d}:{i//60%60:02d}:{i%60:02d}+00:00',**extra)
        self.store.create(value)
        return value

    def groups(self, **params):
        response=self.client.get('/api/run-plan-groups',params=params)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_group_before_pagination_same_names_latest_and_renamed_plan(self):
        for i in range(145):self.add(f'a{i:03d}','plan-a',i)
        latest=self.add('a-latest','plan-a',200,status='queued')
        for i in range(12):self.add(f'b{i:03d}',f'plan-b{i}',i+1)
        stamp=latest['created_at']
        self.store.put_plan({'id':'plan-a','name':'Renamed plan','created_at':stamp,'updated_at':stamp})
        first=self.groups(page=1,page_size=2)
        self.assertEqual(first['total'],13)
        self.assertEqual(first['items'][0]['run_count'],146)
        self.assertEqual(first['items'][0]['latest_run'],latest)
        self.assertEqual(first['items'][0]['name'],'Renamed plan')
        self.assertEqual(self.groups(page_size=100,q='Renamed')['items'][0]['plan_id'],'plan-a')
        # Status describes the latest run, never an older matching run.
        self.assertEqual(self.groups(status='completed')['total'],12)
        self.assertEqual(self.groups(status='queued')['items'][0]['run_count'],146)
        ids=[]
        for page in range(1,8):ids.extend(row['id'] for row in self.groups(page=page,page_size=2)['items'])
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(ids),13)

    def test_exact_plan_history_all_pages_and_latest_deletion(self):
        for i in range(105):self.add(f'a{i:03d}','plan-a',i)
        self.add('unrelated','plan-aa',300)
        pages=[]
        for page in range(1,12):
            response=self.client.get('/api/runs',params={'plan_id':'plan-a','page':page,'page_size':10})
            self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['total'],105);pages.extend(data['items'])
        self.assertEqual(len(pages),105)
        self.assertEqual({r['plan_id'] for r in pages},{'plan-a'})
        self.assertEqual(pages[0]['id'],'a104')
        self.assertEqual(pages[-1]['id'],'a000')
        self.store.delete_run('a104')
        value=self.groups(q='plan-a',page_size=100)['items']
        group=next(v for v in value if v['plan_id']=='plan-a')
        self.assertEqual(group['run_count'],104)
        self.assertEqual(group['latest_run']['id'],'a103')
        self.assertIsInstance(self.client.get('/api/runs').json(),list)

    def test_deleted_plans_missing_ids_empty_groups_and_ties(self):
        self.assertEqual(self.groups()['total'],0)
        self.add('a','deleted',1);self.add('z','deleted',1)
        self.add('legacy-a',None,2);self.add('legacy-b','',2)
        stamp='2026-11-13T00:00:01+00:00'
        self.store.put_plan({'id':'deleted','name':'Deleted plan','created_at':stamp,'updated_at':stamp})
        self.store.delete_plan('deleted')
        result=self.groups(page=99,page_size=100)
        self.assertEqual(result['page'],1)
        self.assertEqual(result['total'],3)
        group=next(v for v in result['items'] if v['plan_id']=='deleted')
        self.assertEqual(group['latest_run']['id'],'z')
        self.assertEqual(group['name'],'Repeated name')
        self.assertEqual(group['run_count'],2)
        self.assertEqual(self.groups(q="' OR 1=1 --")['total'],0)
        self.assertEqual(self.client.get('/api/run-plan-groups?page_size=101').status_code,422)


class RunHistoryWorkspaceTests(unittest.TestCase):
    setUp=workspace_fixtures.WorkspaceTests.setUp
    drop_workspace_schemas=workspace_fixtures.WorkspaceTests.drop_workspace_schemas
    client=workspace_fixtures.WorkspaceTests.client

    def test_group_and_detail_reads_stay_in_selected_workspace(self):
        owner_store=self.manager.runtime(self.owner_status['personal_workspace_id']).store
        member_store=self.manager.runtime(self.member_status['personal_workspace_id']).store
        stamp='2026-11-13T00:00:01+00:00'
        owner_store.create(run('owner-run','same-plan',stamp))
        member_store.create(run('member-run','same-plan',stamp))
        for client,identifier in [(self.owner,'owner-run'),(self.member,'member-run')]:
            response=client.get('/api/run-plan-groups')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['items'][0]['latest_run']['id'],identifier)
            values=client.get('/api/runs?plan_id=same-plan&page=1').json()
            self.assertEqual([r['id'] for r in values['items']],[identifier])
