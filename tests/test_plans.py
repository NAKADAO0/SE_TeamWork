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
