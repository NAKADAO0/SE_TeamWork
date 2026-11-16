import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from backend.api import create_app


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "plans.db"

    def test_restart_and_missing_revision(self):
        for _ in range(2):
            with TestClient(create_app(self.path)) as client:
                response = client.get("/plans/chat-demo?revision=1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["plan"]["tasks"]), 8)
                self.assertEqual(client.get("/plans/chat-demo?revision=2").status_code, 404)
                self.assertEqual(client.get("/plans/unknown").status_code, 404)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0], 1)
        finally:
            db.close()

    def test_simultaneous_editors_and_restart(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with TestClient(create_app(self.path)) as client:
            initial = client.get("/plans/chat-demo").json()["plan"]
            gate = Barrier(2)

            def save(title):
                plan = dict(initial, title=title)
                gate.wait(timeout=5)
                return client.put("/plans/chat-demo", json={"expected_revision": 1, "plan": plan})

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(save, ["编辑 A", "编辑 B"]))
            self.assertEqual(sorted(r.status_code for r in results), [200, 409])
            winner = next(r.json() for r in results if r.status_code == 200)
            loser = next(r.json() for r in results if r.status_code == 409)
            self.assertEqual(loser["detail"]["current_revision"], 2)
            self.assertNotIn("batches", winner["plan"])
        with TestClient(create_app(self.path)) as client:
            self.assertEqual(client.get("/plans/chat-demo?revision=1").json()["plan"], initial)
            self.assertEqual(client.get("/plans/chat-demo?revision=2").json(), winner)
            self.assertEqual(client.get("/plans/chat-demo").json(), winner)
