"""端到端集成测试：DesignSpec（JSON/Markdown）-> PM 分解 -> 权威校验 -> 编排 -> 图谱 -> 确认与导出。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api import create_app

ROOT = Path(__file__).resolve().parents[1]


class EndToEndWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "e2e.sqlite3"

    def test_three_products_json_and_markdown_end_to_end(self):
        chat_spec = json.loads((ROOT / "examples/contracts/chat/design-spec.json").read_text(encoding="utf-8"))
        community_spec = json.loads((ROOT / "examples/contracts/community/design-spec.json").read_text(encoding="utf-8"))
        unseen_md = (ROOT / "examples/unseen-spec/spec.md").read_text(encoding="utf-8")

        cases = [
            ("chat", {"draft": chat_spec}, "聊天"),
            ("community", {"draft": community_spec}, "内容社区"),
            ("unseen-md", {"text": unseen_md}, "校园失物招领"),
        ]

        with TestClient(create_app(self.db_path)) as client:
            for name, payload, expected_keyword in cases:
                with self.subTest(product=name):
                    # 1. 提交规格并检查无阻断缺口
                    resp = client.post("/specs", json=payload)
                    self.assertEqual(resp.status_code, 201, resp.text)
                    spec_data = resp.json()
                    self.assertEqual(spec_data["issues"]["errors"], [], spec_data)
                    self.assertEqual(spec_data["issues"]["warnings"], [], spec_data)
                    sid = spec_data["spec_id"]

                    # 2. 确认规格锁定输入
                    confirm_resp = client.post(f"/specs/{sid}/confirm", json={"expected_version": 1})
                    confirm_resp.raise_for_status()
                    self.assertTrue(confirm_resp.json()["confirmed"])

                    # 3. 发起规划作业（离线结构化规则模式）
                    job_resp = client.post(
                        "/planning-jobs",
                        json={
                            "spec_ref": {"spec_id": sid, "version": 1},
                            "template_ref": "default@1",
                            "config": {"max_concurrency": 2, "role_capacity": {"backend": 1, "frontend": 1, "qa": 1}},
                            "mode": "offline_rule",
                        },
                    )
                    self.assertEqual(job_resp.status_code, 202, job_resp.text)
                    job = client.get(f"/planning-jobs/{job_resp.json()['job_id']}").json()
                    self.assertEqual(job["status"], "succeeded", job)
                    pid = job["plan_ref"]["plan_id"]

                    # 4. 读取任务计划并校验
                    plan_doc = client.get(f"/plans/{pid}?revision=1").json()["plan"]
                    self.assertGreaterEqual(len(plan_doc["tasks"]), 4)
                    val_report = client.post(f"/plans/{pid}/validate", json={"revision": 1}).json()
                    self.assertEqual(val_report["errors"], [], val_report)
                    self.assertEqual(val_report["coverage"]["implementation_ratio"], 1.0)
                    self.assertEqual(val_report["coverage"]["verification_ratio"], 1.0)

                    # 5. 编排（默认 qa=1 vs 扩容 qa=2）
                    exec1 = client.post(f"/plans/{pid}/compile", json={"revision": 1}).json()
                    self.assertTrue(exec1["compiled"])
                    self.assertTrue(exec1["batch_details"])
                    self.assertCountEqual(
                        [t for b in exec1["batches"] for t in b],
                        [t["id"] for t in plan_doc["tasks"]],
                    )
                    self.assertEqual(exec1["batches"][0], ["T-CONTRACT"])
                    self.assertEqual(exec1["batches"][-1], ["T-E2E"])
                    self.assertEqual(sorted(exec1["nodes"]), sorted(t["id"] for t in plan_doc["tasks"]))

                    exec2 = client.post(
                        f"/plans/{pid}/compile",
                        json={"revision": 1, "config": {"max_concurrency": 3, "role_capacity": {"backend": 1, "frontend": 1, "qa": 2}}},
                    ).json()
                    self.assertTrue(exec2["compiled"])
                    self.assertLessEqual(len(exec2["batches"]), len(exec1["batches"]))

                    # 6. 图谱读接口与确认、导出一致
                    graph_res = client.get(f"/plans/{pid}/graph?revision=1")
                    graph_res.raise_for_status()
                    self.assertEqual(graph_res.json()["plan"]["batches"], exec2["batches"])

                    client.post(
                        f"/plans/{pid}/confirm",
                        json={"revision": 1, "execution_plan_id": exec2["execution_plan_id"]},
                    ).raise_for_status()

                    export_json = client.get(f"/plans/{pid}/export?revision=1&format=json").json()
                    self.assertEqual(export_json["status"], "confirmed")
                    export_md = client.get(f"/plans/{pid}/export?revision=1&format=markdown").text
                    self.assertIn(expected_keyword, export_md)

    def test_vague_one_sentence_input_is_rejected_by_gap_check(self):
        with TestClient(create_app(self.db_path)) as client:
            for payload in ({"draft": {"product": {"name": "我要做微信"}}}, {"text": "我要做微信"}):
                resp = client.post("/specs", json=payload)
                self.assertEqual(resp.status_code, 201)
                data = resp.json()
                codes = {item["code"] for item in data["issues"]["errors"]}
                self.assertIn("MISSING_SCOPE", codes)
                self.assertIn("MISSING_REQUIREMENTS", codes)
                confirm = client.post(f"/specs/{data['spec_id']}/confirm", json={"expected_version": 1})
                self.assertEqual(confirm.status_code, 422)

    def test_tasks_put_and_fixture_replay_and_static_pages(self):
        chat_spec = json.loads((ROOT / "examples/contracts/chat/design-spec.json").read_text(encoding="utf-8"))
        with TestClient(create_app(self.db_path)) as client:
            # 静态页面和健康检查可访问
            for route in ("/", "/health", "/frontend/", "/graph.html", "/examples.html", "/guide.html"):
                self.assertEqual(client.get(route).status_code, 200, route)

            # 样例回放模式保留 fixture_replay 标识
            sid = client.post("/specs", json={"draft": chat_spec}).json()["spec_id"]
            client.post(f"/specs/{sid}/confirm", json={"expected_version": 1}).raise_for_status()
            job = client.post(
                "/planning-jobs",
                json={"spec_ref": {"spec_id": sid, "version": 1}, "mode": "fixture_replay"},
            )
            self.assertEqual(job.status_code, 202)
            done = client.get(f"/planning-jobs/{job.json()['job_id']}").json()
            self.assertEqual(done["status"], "succeeded")
            self.assertEqual(done["generation_mode"], "fixture_replay")
            pid = done["plan_ref"]["plan_id"]

            # 支持前端契约的 PUT {expected_revision, tasks}，并验证 409 乐观锁
            plan_r1 = client.get(f"/plans/{pid}?revision=1").json()["plan"]
            edited_tasks = list(plan_r1["tasks"])
            edited_tasks[0] = {**edited_tasks[0], "title": "更新后的契约定义"}
            put_ok = client.put(f"/plans/{pid}", json={"expected_revision": 1, "tasks": edited_tasks})
            self.assertEqual(put_ok.status_code, 200)
            self.assertEqual(put_ok.json()["revision"], 2)

            put_stale = client.put(f"/plans/{pid}", json={"expected_revision": 1, "tasks": edited_tasks})
            self.assertEqual(put_stale.status_code, 409)
            self.assertEqual(put_stale.json()["detail"]["current_revision"], 2)

    def test_live_llm_mode_when_env_configured_uses_http_provider_with_repair(self):
        chat_spec = json.loads((ROOT / "examples/contracts/chat/design-spec.json").read_text(encoding="utf-8"))
        good_plan = json.loads((ROOT / "examples/contracts/chat/task-plan.json").read_text(encoding="utf-8"))
        mock_payload = json.dumps({
            "tasks": good_plan["tasks"],
            "assumptions": good_plan["assumptions"],
            "clarifications": [],
        }, ensure_ascii=False)

        with patch.dict(
            "os.environ",
            {"PM_BASE_URL": "http://mock-llm.local/v1", "PM_API_KEY": "test-key", "PM_MODEL": "mock-model"},
        ), patch("backend.app.planner.providers.HttpProvider.complete", return_value=mock_payload) as mock_complete:
            with TestClient(create_app(self.db_path)) as client:
                self.assertTrue(client.get("/health").json()["live_llm_configured"])
                sid = client.post("/specs", json={"draft": chat_spec}).json()["spec_id"]
                client.post(f"/specs/{sid}/confirm", json={"expected_version": 1}).raise_for_status()
                job = client.post(
                    "/planning-jobs",
                    json={"spec_ref": {"spec_id": sid, "version": 1}, "mode": "live_llm"},
                )
                self.assertEqual(job.status_code, 202, job.text)
                done = client.get(f"/planning-jobs/{job.json()['job_id']}").json()
                self.assertEqual(done["status"], "succeeded", done)
                self.assertEqual(done["generation_mode"], "live_llm")
                self.assertEqual(mock_complete.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
