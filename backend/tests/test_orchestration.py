"""编排器测试（成员 4）：拓扑分层、资源约束批次、槽位绑定、等待原因与契约对齐。"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from backend.app.orchestration import compile_plan
from backend.app.validation import validate_execution_plan

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> dict:
    return json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))


class OrchestrationTest(unittest.TestCase):
    def test_chat_contract_plan_compiles_and_passes_execution_validation(self) -> None:
        spec = _load("examples/contracts/chat/design-spec.json")
        plan = _load("examples/contracts/chat/task-plan.json")
        template = _load("templates/default-workflow.json")
        report = _load("examples/contracts/chat/validation-report.json")

        compiled = compile_plan(
            plan,
            spec=spec,
            template=template,
            validation_report=report,
            execution_plan_id="EXE-CHAT-TEST",
        )
        self.assertTrue(compiled["compiled"])
        self.assertEqual(
            compiled["batches"],
            [["C1"], ["C2", "C3"], ["C4", "C6"], ["C5"], ["C7"], ["C8"]],
        )
        self.assertEqual(len(compiled["nodes"]), 8)
        self.assertEqual(len(compiled["edges"]), 11)

        # 验证生成的 ExecutionPlan 严格符合成员 3 的 Schema 与校验器
        exec_report = validate_execution_plan(
            compiled["schema_execution_plan"],
            task_plan=plan,
            spec=spec,
            template=template,
            validation_report=report,
        )
        self.assertFalse(exec_report.blocked, [i.message for i in exec_report.issues])
        self.assertEqual(exec_report.issues, [])

    def test_determinism_and_role_capacity_change(self) -> None:
        tasks = [
            {"id": "A", "title": "后端A", "role": "backend", "priority": 1, "depends_on": []},
            {"id": "B", "title": "前端B", "role": "frontend", "priority": 1, "depends_on": []},
            {"id": "C", "title": "测试A", "role": "qa", "priority": 1, "depends_on": [{"task_id": "A", "reason": "测A"}]},
            {"id": "D", "title": "测试B", "role": "qa", "priority": 1, "depends_on": [{"task_id": "B", "reason": "测B"}]},
            {"id": "E", "title": "联调", "role": "qa", "priority": 2, "depends_on": [{"task_id": "C", "reason": "等C"}, {"task_id": "D", "reason": "等D"}]},
        ]
        plan = {"plan_id": "PLAN-CAP", "revision": 1, "tasks": tasks}

        narrow1 = compile_plan(copy.deepcopy(plan), config_override={"max_concurrency": 3, "role_capacity": {"backend": 1, "frontend": 1, "qa": 1}})
        narrow2 = compile_plan(copy.deepcopy(plan), config_override={"max_concurrency": 3, "role_capacity": {"backend": 1, "frontend": 1, "qa": 1}})
        wide = compile_plan(copy.deepcopy(plan), config_override={"max_concurrency": 3, "role_capacity": {"backend": 1, "frontend": 1, "qa": 2}})

        # 同输入完全一致（去除随机 execution_plan_id）
        self.assertEqual(narrow1["batches"], narrow2["batches"])
        self.assertEqual(narrow1["bindings"], narrow2["bindings"])
        self.assertEqual(narrow1["batch_details"], narrow2["batch_details"])

        # 提高 qa 容量后批次缩短为 3 批，拓扑层和依赖边不变
        self.assertEqual(narrow1["batches"], [["A", "B"], ["C", "D" if False else "C"][:1], ["D"], ["E"]])
        self.assertEqual(wide["batches"], [["A", "B"], ["C", "D"], ["E"]])
        self.assertEqual(narrow1["edges"], wide["edges"])
        self.assertEqual(narrow1["topological_levels"], wide["topological_levels"])

        # 区分资源等待与依赖等待
        batch2_waiting = {w["task_id"]: w["reason"] for w in narrow1["batch_details"][1]["waiting"]}
        self.assertIn("qa 容量已满", batch2_waiting["D"])
        self.assertIn("等待前置", batch2_waiting["E"])

    def test_blocked_when_role_capacity_zero_or_cycle(self) -> None:
        cycle_plan = {
            "plan_id": "PLAN-CYC",
            "revision": 1,
            "tasks": [
                {"id": "T1", "role": "backend", "depends_on": [{"task_id": "T2", "reason": "loop"}]},
                {"id": "T2", "role": "backend", "depends_on": [{"task_id": "T1", "reason": "loop"}]},
            ],
        }
        res = compile_plan(cycle_plan)
        self.assertFalse(res["compiled"])
        self.assertEqual(len(res["blocked"]), 2)

        zero_cap = {
            "plan_id": "PLAN-ZERO",
            "revision": 1,
            "tasks": [{"id": "T1", "role": "qa", "depends_on": []}],
        }
        res_zero = compile_plan(zero_cap, config_override={"max_concurrency": 1, "role_capacity": {"qa": 0}})
        self.assertFalse(res_zero["compiled"])
        self.assertIn("容量", res_zero["blocked"][0]["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
