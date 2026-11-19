from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from backend.api import create_app
from backend.delivery import compile_with_orchestrator


class ReviewGatesTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.path = Path(folder) / "test.db"
        self.client = self.enterContext(TestClient(create_app(self.path, validator=lambda _: [])))
        self.url = "/plans/chat-demo/review"
        self.sha = "1" * 40

    def confirm(self):
        root = "/plans/chat-demo"
        self.assertEqual(self.client.post(root + "/validate", json={"revision": 1}).status_code, 200)
        compiled = self.client.post(root + "/compile", json={"revision": 1})
        self.assertEqual(compiled.status_code, 200, compiled.text)
        confirmation = self.client.post(root + "/confirm", json={
            "revision": 1, "execution_plan_id": compiled.json()["execution_plan_id"],
        })
        self.assertEqual(confirmation.status_code, 200, confirmation.text)

    def start(self, sha=None, previous=None):
        return self.client.post(self.url, json={
            "revision": 1, "commit_sha": sha or self.sha, "previous_sha": previous,
            "author": "demo-author", "change_ref": "demo-pr",
        })

    def report(self, check, *, sha=None, attempt=1, **data):
        return self.client.post(self.url + "/reports", json={
            "revision": 1, "commit_sha": sha or self.sha, "check": check, "attempt": attempt,
            "outcome": "completed", **data,
        })

    def decide(self, decision="approve", *, reviewer="demo-reviewer", sha=None):
        return self.client.post(self.url + "/decision", json={
            "revision": 1, "commit_sha": sha or self.sha, "reviewer": reviewer,
            "decision": decision, "reason": "checked the evidence",
        })

    def pass_checks(self, sha=None):
        for name in ("quality", "logic", "dependencies"):
            response = self.report(name, sha=sha)
            self.assertEqual(response.status_code, 200, response.text)
        return response.json()["current"]

    def test_confirmation_and_two_distinct_gates(self):
        self.assertEqual(self.start().status_code, 409)
        self.confirm()
        self.assertEqual(self.start().status_code, 201)
        self.assertEqual(self.decide().status_code, 409)
        self.assertEqual(self.pass_checks()["status"], "awaiting_human")
        self.assertEqual(self.decide(reviewer="DEMO-AUTHOR").status_code, 403)
        approved = self.decide()
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertTrue(approved.json()["downstream_ready"])
        with TestClient(create_app(self.path)) as reopened:
            current = reopened.get(self.url, params={"revision": 1}).json()
        self.assertEqual(current["current"]["decision"]["reviewer"], "demo-reviewer")
        self.assertTrue(current["downstream_ready"])

    def test_new_commit_invalidates_previous_approval(self):
        self.confirm()
        self.start()
        self.pass_checks()
        self.decide()
        new_sha = "2" * 40
        self.assertEqual(self.start(new_sha).status_code, 409)
        state = self.start(new_sha, self.sha).json()
        self.assertFalse(state["downstream_ready"])
        self.assertEqual(state["history"][0]["status"], "approved")
        self.assertEqual(self.decide().status_code, 409)
        self.assertEqual(self.report("quality").status_code, 409)
        self.assertEqual(self.decide(sha=new_sha).status_code, 409)
        plan = self.client.get("/plans/chat-demo").json()["plan"]
        self.assertEqual(self.client.put("/plans/chat-demo", json={
            "expected_revision": 1, "plan": plan,
        }).status_code, 200)
        self.assertEqual(self.report("logic", sha=new_sha).status_code, 409)
        self.assertFalse(self.client.get(self.url, params={"revision": 1}).json()["downstream_ready"])

    def test_blocking_finding_and_bounded_retry(self):
        self.confirm()
        self.start()
        self.report("logic", findings=[{"severity": "high", "message": "Missing acceptance evidence"}])
        self.report("dependencies")
        for attempt in (1, 2):
            failed = self.report("quality", attempt=attempt, outcome="transient_error", note="CI timeout")
            self.assertEqual(failed.json()["current"]["status"], "retryable")
            retry = self.client.post(self.url + "/retry", json={
                "revision": 1, "commit_sha": self.sha, "check": "quality", "attempt": attempt,
            })
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertEqual(self.report("quality", attempt=attempt).status_code, 409)
        blocked = self.report("quality", attempt=3).json()
        self.assertEqual(blocked["current"]["status"], "changes_requested")
        self.assertEqual(len(blocked["current"]["checks"]["quality"]), 3)
        self.assertEqual(self.decide().status_code, 409)

    def test_rework_limit_shared_with_human_gate(self):
        self.confirm()
        previous = None
        for index in range(1, 5):
            sha = str(index) * 40
            self.assertEqual(self.start(sha, previous).status_code, 201)
            if index == 1:
                self.report("quality", sha=sha)
                self.report("logic", sha=sha)
                state = self.report("dependencies", sha=sha, findings=[
                    {"severity": "critical", "message": "Dependency evidence failed"},
                ]).json()
            else:
                self.pass_checks(sha)
                state = self.decide("request_changes", sha=sha).json()
            self.assertEqual(state["current"]["status"], "changes_requested" if index < 4 else "needs_attention")
            self.assertFalse(state["downstream_ready"])
            previous = sha
        self.assertEqual(self.start("5" * 40, previous).status_code, 409)

    def test_inflight_recompile_cannot_replace_confirmed_snapshot(self):
        plan_url = "/plans/chat-demo"
        response = self.client.post(plan_url + "/compile", json={"revision": 1})
        old_id = response.json()["execution_plan_id"]
        entered, release = Event(), Event()

        def pause_compile(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("recompile did not resume")
            return compile_with_orchestrator(*args, **kwargs)

        with patch("backend.delivery.compile_with_orchestrator", side_effect=pause_compile):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.client.post, plan_url + "/compile", json={
                    "revision": 1, "config": {"max_concurrency": 1},
                })
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(self.client.post(plan_url + "/confirm", json={
                        "revision": 1, "execution_plan_id": old_id,
                    }).status_code, 200)
                finally:
                    release.set()
                self.assertEqual(pending.result(timeout=5).status_code, 409)
        current = self.client.post(plan_url + "/compile", json={"revision": 1})
        self.assertEqual(current.json()["execution_plan_id"], old_id)
        self.assertEqual(self.start().json()["current"]["execution_plan_id"], old_id)

    def test_exhausted_check_can_be_stopped_without_approval(self):
        self.confirm()
        self.start()
        for attempt in (1, 2, 3):
            result = self.report("quality", attempt=attempt, outcome="transient_error", note="CI timeout")
            self.assertEqual(result.status_code, 200, result.text)
            if attempt < 3:
                self.assertEqual(self.client.post(self.url + "/retry", json={
                    "revision": 1, "commit_sha": self.sha, "check": "quality", "attempt": attempt,
                }).status_code, 200)
        self.assertEqual(result.json()["current"]["status"], "needs_attention")
        stopped = self.client.post(self.url + "/stop", json={
            "revision": 1, "commit_sha": self.sha, "operator": "demo-operator", "reason": "manual follow-up",
        }).json()
        self.assertEqual(stopped["current"]["status"], "stopped")
        self.assertEqual(stopped["current"]["intervention"]["operator"], "demo-operator")
        self.assertFalse(stopped["downstream_ready"])

    def test_plan_export_remains_a_planning_snapshot(self):
        self.confirm()
        url = "/plans/chat-demo/export?revision=1"
        before = self.client.get(url).json()
        self.start()
        self.pass_checks()
        self.decide()
        self.assertEqual(self.client.get(url).json(), before)

    def test_live_review_uses_model_and_cannot_be_forged_by_demo_reports(self):
        self.confirm()
        resp = self.client.post(self.url, json={
            "revision":1,"commit_sha":self.sha,"author":"developer","change_ref":"PR-1",
            "mode":"live","diff":"+example code",
        })
        self.assertEqual(resp.status_code,201,resp.text)
        self.assertNotIn("diff",resp.json()["current"])
        self.assertEqual(self.report("logic").status_code,409)
        self.assertEqual(self.decide().status_code,409)
        reports = {key: {"note":"已复核提交差异","findings":[]} for key in ("quality","logic","dependencies")}
        with patch("backend.review.generate_code_review",return_value=reports) as model:
            checked = self.client.post(self.url+"/ai",json={"revision":1,"commit_sha":self.sha})
            self.assertTrue(model.called)
        self.assertEqual(checked.status_code,200,checked.text)
        self.assertEqual(checked.json()["current"]["status"],"awaiting_human")
        self.assertFalse(checked.json()["downstream_ready"])
        self.assertTrue(self.decide().json()["downstream_ready"])

    def test_live_ai_recheck_rejects_late_result_after_new_commit(self):
        self.confirm()
        submission={"revision":1,"author":"developer","change_ref":"PR-1","mode":"live","diff":"+example"}
        self.assertEqual(self.client.post(self.url,json=submission | {"commit_sha":self.sha}).status_code,201)
        entered, release = Event(), Event()
        def model(*_):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test synchronization timed out")
            return {k: {"note":"review", "findings":[]} for k in ("quality","logic","dependencies")}
        with patch("backend.review.generate_code_review",side_effect=model):
            with ThreadPoolExecutor(max_workers=1) as pool:
                running=pool.submit(self.client.post,self.url+"/ai",json={"revision":1,"commit_sha":self.sha})
                try:
                    self.assertTrue(entered.wait(5))
                    new="2"*40
                    resp=self.client.post(self.url,json=submission | {"commit_sha":new,"previous_sha":self.sha,"diff":"+next"})
                    self.assertEqual(resp.status_code,201)
                    self.assertEqual(self.client.post(self.url,json={
                        "revision":1,"author":"developer","change_ref":"PR-1","commit_sha":"3"*40,"previous_sha":new,
                    }).status_code,409)
                finally:
                    release.set()
                self.assertEqual(running.result(timeout=5).status_code,409)
        snap=self.client.get(self.url,params={"revision":1}).json()
        self.assertEqual(snap["current"]["commit_sha"],new)
        self.assertFalse(snap["downstream_ready"])
        self.assertEqual(snap["current"]["checks"]["logic"][0]["outcome"],"pending")

    def test_human_rejection_is_terminal(self):
        self.confirm()
        self.start()
        self.pass_checks()
        rejected = self.decide("reject")
        self.assertEqual(rejected.json()["current"]["status"], "rejected")
        self.assertFalse(rejected.json()["downstream_ready"])
        self.assertEqual(self.decide().status_code, 409)
        self.assertEqual(self.start("2" * 40, self.sha).status_code, 409)


if __name__ == "__main__":
    unittest.main()
