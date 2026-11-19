import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.api import create_app
from backend.store import PlanStore


class AgentBridgeTest(TestCase):
    def test_uses_confirmed_snapshot_and_rejects_a_foreign_run(self):
        with TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "test.sqlite3"
            with TestClient(create_app(db_file)) as client:
                path = "/plans/chat-demo/agent-run"
                self.assertEqual(client.post(path, json={"revision": 1, "mode": "demo"}).status_code, 409)
                with closing(PlanStore(db_file).connect()) as db, db:
                    db.executemany("INSERT INTO artifacts VALUES ('chat-demo', 1, ?, ?)", [
                        ("confirmation", json.dumps({"execution_plan_id": "example"})),
                        ("execution", json.dumps({"execution_plan_id": "example", "batches": [["C1"]]})),
                    ])
                with patch("backend.agent_bridge.lab1_request") as upstream:
                    upstream.return_value = {"id": "a" * 14}
                    self.assertEqual(client.post(path, json={"revision": 1, "mode": "demo"}).status_code, 201)
                    url, body = upstream.call_args.args
                    self.assertEqual(url, "/api/executions")
                    self.assertEqual(body["bundle"]["execution"]["batches"], [["C1"]])
                    upstream.reset_mock()
                    upstream.return_value = {"execution": {"plan_id": "other", "revision": 1}}
                    response = client.post(path + "/" + "a" * 14 + "/interrupt?revision=1", json={"text": "hold"})
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(upstream.call_count, 1)
                    upstream.reset_mock()
                    upstream.return_value = {"id": "a" * 14, "execution": {
                        "plan_id": "chat-demo", "revision": 1, "id": "example"}}
                    self.assertEqual(client.post(path + "/" + "a" * 14 + "/interrupt?revision=1",
                                                 json={"text": "check overlap"}).status_code, 200)
                    self.assertEqual(upstream.call_args.args[1], {"text": "check overlap"})
