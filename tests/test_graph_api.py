from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from backend.api import create_app


class GraphReadTests(unittest.TestCase):
    def test_revision_snapshot_and_missing_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Path(directory) / 'plans.db')) as client:
                initial = client.get('/plans/chat-demo/graph').json()
                self.assertEqual(initial['plan']['status'], 'compiled')
                plan = deepcopy(initial['plan'])
                plan['tasks'][0]['title'] = '修改后的任务'
                client.put('/plans/chat-demo', json={'expected_revision': 1, 'plan': plan}).raise_for_status()
                self.assertEqual(client.get('/plans').json()[0]['latest_revision'], 2)
                self.assertEqual(client.get('/plans/chat-demo/revisions').json(), [{'revision': 2}, {'revision': 1}])
                latest = client.get('/plans/chat-demo/graph').json()
                self.assertEqual(latest['revision'], 2)
                self.assertEqual(latest['plan']['batches'], [])
                self.assertEqual(latest['plan']['status'], 'draft')
                self.assertEqual(client.get('/plans/chat-demo/graph?revision=1').json(), initial)
                execution = client.post('/plans/chat-demo/compile', json={'revision': 2}).json()
                compiled = client.get('/plans/chat-demo/graph').json()
                self.assertEqual(compiled['execution'], execution)
                self.assertEqual(compiled['plan']['batches'], execution['batches'])
                self.assertEqual(compiled['plan']['status'], 'compiled')
                client.post('/plans/chat-demo/confirm', json={'revision': 2, 'execution_plan_id': execution['execution_plan_id']}).raise_for_status()
                self.assertEqual(client.get('/plans/chat-demo/graph').json()['plan']['status'], 'confirmed')
                self.assertEqual(len(client.get('/plans/chat-demo/revisions').json()), 2)
                self.assertEqual(client.get('/plans/missing/revisions').status_code, 404)
                self.assertEqual(client.get('/plans/chat-demo/graph?revision=99').status_code, 404)

    def test_local_browser_can_read_but_unrelated_origin_cannot(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(Path(directory) / 'plans.db')) as client:
                allowed = client.get('/plans', headers={'Origin': 'http://127.0.0.1:8000'})
                self.assertEqual(allowed.headers['access-control-allow-origin'], 'http://127.0.0.1:8000')
                unrelated = client.get('/plans', headers={'Origin': 'https://example.com'})
                self.assertNotIn('access-control-allow-origin', unrelated.headers)

    def assert_graph_matches_export(self, client, revision):
        route = '/plans/chat-demo'
        graph_response = client.get(f'{route}/graph?revision={revision}')
        export_response = client.get(f'{route}/export?revision={revision}&format=json')
        graph_response.raise_for_status()
        export_response.raise_for_status()
        graph, bundle = graph_response.json(), export_response.json()
        self.assertEqual((graph['plan_id'], graph['revision']), (bundle['plan_id'], bundle['revision']))
        self.assertEqual(graph['plan']['tasks'], bundle['plan']['tasks'])
        self.assertEqual(graph['plan']['spec'], bundle['plan']['spec'])
        self.assertEqual(graph['execution'], bundle['execution'])
        self.assertEqual(graph['plan']['batches'], bundle['execution']['batches'])
        tasks = graph['plan']['tasks']
        ids = [task['id'] for task in tasks]
        expected_edges = {(dep, task['id']) for task in tasks for dep in task['depends_on']}
        actual_edges = {(edge['from'], edge['to']) for edge in bundle['execution']['edges']}
        self.assertEqual(expected_edges, actual_edges)
        self.assertEqual(len(bundle['execution']['edges']), len(expected_edges))
        self.assertCountEqual(ids, bundle['execution']['nodes'])
        self.assertCountEqual(ids, [tid for batch in graph['plan']['batches'] for tid in batch])
        self.assertEqual(bundle['execution']['validation_ref'], bundle['validation']['validation_ref'])
        self.assertEqual(bundle['confirmation']['execution_plan_id'], graph['execution']['execution_plan_id'])
        markdown = client.get(f'{route}/export?revision={revision}&format=markdown')
        markdown.raise_for_status()
        self.assertEqual(json.loads(markdown.text.split('## 完整快照\n\n', 1)[1]), bundle)
        return bundle

    def test_graph_and_exports_match_across_revisions_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'plans.db'
            with TestClient(create_app(database)) as client:
                route = '/plans/chat-demo'
                execution = client.post(f'{route}/compile', json={'revision': 1})
                execution.raise_for_status()
                client.post(f'{route}/confirm', json={
                    'revision': 1, 'execution_plan_id': execution.json()['execution_plan_id'],
                }).raise_for_status()
                original = self.assert_graph_matches_export(client, 1)

                draft = deepcopy(original['plan'])
                draft['capacity'] = 1
                draft['tasks'][0]['title'] = '第二版消息契约'
                draft['tasks'][2]['depends_on'] = ['C2']
                draft['tasks'].append({
                    'id': 'AUDIT', 'title': '复核完整交付', 'role': 'qa',
                    'depends_on': ['C8'], 'acceptance': '交付包内容与固定修订一致',
                })
                client.put(route, json={'expected_revision': 1, 'plan': draft}).raise_for_status()
                self.assertEqual(client.get(f'{route}/export?revision=2').status_code, 409)
                execution = client.post(f'{route}/compile', json={'revision': 2})
                execution.raise_for_status()
                client.post(f'{route}/confirm', json={
                    'revision': 2, 'execution_plan_id': execution.json()['execution_plan_id'],
                }).raise_for_status()
                latest = self.assert_graph_matches_export(client, 2)
                self.assertEqual(len(latest['plan']['tasks']), 9)
                self.assertTrue(all(len(batch) == 1 for batch in latest['execution']['batches']))
                self.assertNotEqual(original['execution']['edges'], latest['execution']['edges'])
                self.assertNotEqual(original['execution']['batches'], latest['execution']['batches'])
                self.assertEqual(client.get(f'{route}/graph').json()['revision'], 2)
                self.assertEqual(self.assert_graph_matches_export(client, 1), original)

            with TestClient(create_app(database)) as client:
                self.assertEqual(self.assert_graph_matches_export(client, 1), original)
                self.assertEqual(self.assert_graph_matches_export(client, 2), latest)
                self.assertEqual(client.get('/plans/chat-demo/revisions').json(), [
                    {'revision': 2}, {'revision': 1},
                ])
