"""校验器测试：覆盖 V04–V06 验收场景与七类检查面。

用例直接对应设计文档 §8.3：

* V04：删除某需求的验证任务 → 覆盖错误定位到需求与相关任务；
* V05：缺失前置/自依赖/环 → 拒绝编排，环给出真实闭合路径；
* V06：输入引用不存在的任务产物 → 定位 inputs 与生产者并阻止确认；
* V07：同输入重复校验结果一致（确定性）。
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.contracts import content_digest, to_jsonable
from backend.app.validation import codes
from backend.app.validation.validator import (
    validate_design_spec,
    validate_execution_plan,
    validate_task_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> Any:
    with (REPO_ROOT / relative).open("r", encoding="utf-8", newline="") as handle:
        return json.load(handle)


def _copy(document: Any) -> Any:
    return copy.deepcopy(document)


def _chat_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load("examples/contracts/chat/design-spec.json"),
        _load("examples/contracts/chat/task-plan.json"),
        _load("templates/default-workflow.json"),
    )


def _find(report: Any, code: str) -> Any:
    for issue in report.issues:
        if issue.code == code:
            return issue
    raise AssertionError(f"报告中缺少错误码 {code}；实际为 {[issue.code for issue in report.issues]}")


def _paths(report: Any) -> list[tuple[str, str]]:
    return [(issue.code, issue.field_path) for issue in report.issues]


class ValidSamplesTest(unittest.TestCase):
    def test_chat_plan_has_no_issues_and_full_coverage(self) -> None:
        spec, plan, template = _chat_inputs()
        report = validate_task_plan(plan, spec=spec, template=template)
        self.assertFalse(report.blocked)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.target.kind, "task_plan")
        self.assertEqual(report.target.plan_id, "PLAN-CHAT")
        self.assertEqual(report.target.revision, 1)
        self.assertEqual(report.content_digest, content_digest(plan))
        self.assertIsNotNone(report.coverage)
        assert report.coverage is not None
        self.assertEqual(report.coverage.implementation_ratio, 1.0)
        self.assertEqual(report.coverage.verification_ratio, 1.0)
        self.assertEqual(
            [entry.status for entry in report.coverage.requirements], ["covered", "covered"]
        )
        self.assertEqual(
            report.coverage.requirements[0].implementation_task_ids, ["C1", "C2", "C3"]
        )
        self.assertEqual(report.coverage.requirements[1].verification_task_ids, ["C7"])

    def test_community_plan_respects_role_capacity(self) -> None:
        spec = _load("examples/contracts/community/design-spec.json")
        plan = _load("examples/contracts/community/task-plan.json")
        template = _load("templates/default-workflow.json")
        report = validate_task_plan(plan, spec=spec, template=template)
        self.assertFalse(report.blocked)
        self.assertEqual(report.issues, [])
        self.assertEqual(plan["config"]["role_capacity"]["qa"], 2)

    def test_design_spec_has_no_issues(self) -> None:
        for name in ("chat", "community"):
            with self.subTest(spec=name):
                report = validate_design_spec(_load(f"examples/contracts/{name}/design-spec.json"))
                self.assertFalse(report.blocked)
                self.assertEqual(report.issues, [])

    def test_execution_plan_is_valid(self) -> None:
        spec, plan, template = _chat_inputs()
        execution_plan = _load("examples/contracts/chat/execution-plan.json")
        report_json = _load("examples/contracts/chat/validation-report.json")
        report = validate_execution_plan(
            execution_plan,
            task_plan=plan,
            validation_report=report_json,
            spec=spec,
            template=template,
        )
        self.assertFalse(report.blocked)
        self.assertEqual(report.issues, [])
        self.assertEqual(len(execution_plan["nodes"]), 8)
        self.assertEqual(len(execution_plan["edges"]), 11)
        self.assertEqual(len(execution_plan["batches"]), 6)


class V04CoverageTest(unittest.TestCase):
    def test_missing_verification_task_points_to_requirement(self) -> None:
        spec, _, template = _chat_inputs()
        plan = _load("examples/contracts/invalid/missing-verification/task-plan.json")
        report = validate_task_plan(plan, spec=spec, template=template)
        self.assertTrue(report.blocked)
        issue = _find(report, codes.COVERAGE_VERIFICATION_MISSING)
        self.assertEqual(issue.severity, "error")
        self.assertEqual(issue.object_id, "R2")
        self.assertEqual(issue.field_path, "spec.requirements[1]")
        self.assertEqual(issue.related_ids, ["C4", "C5"])
        self.assertIn("R2", issue.message)
        self.assertTrue(issue.suggested_action)
        # 删除 C7 后 C8 仍引用它：同时报告悬挂依赖与悬挂产物输入。
        found = {code for code, _ in _paths(report)}
        self.assertIn(codes.DEPENDENCY_UNKNOWN, found)
        self.assertIn(codes.ARTIFACT_PRODUCER_UNKNOWN, found)


class V05DependencyTest(unittest.TestCase):
    def test_cycle_reports_real_closed_path(self) -> None:
        spec, _, template = _chat_inputs()
        plan = _load("examples/contracts/invalid/cycle/task-plan.json")
        report = validate_task_plan(plan, spec=spec, template=template)
        self.assertTrue(report.blocked)
        issue = _find(report, codes.DEPENDENCY_CYCLE)
        self.assertEqual(issue.severity, "error")
        self.assertEqual(issue.object_id, "C5")
        self.assertEqual(issue.related_ids, ["C5", "C7", "C5"])
        self.assertIn("C5 → C7 → C5", issue.message)

        # 闭合路径中的每一对相邻节点都必须是计划里真实存在的依赖边。
        dependencies = {
            (task["id"], dependency["task_id"])
            for task in plan["tasks"]
            for dependency in task["depends_on"]
        }
        path = issue.related_ids
        for current, next_task in zip(path, path[1:]):
            self.assertIn((current, next_task), dependencies)
        self.assertEqual(path[0], path[-1])

    def test_self_dependency_is_rejected(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][1]["depends_on"].append(
            {"task_id": "C2", "reason": "错误样例：任务依赖自身"}
        )
        report = validate_task_plan(broken, spec=spec, template=template)
        issue = _find(report, codes.DEPENDENCY_SELF)
        self.assertEqual(issue.object_id, "C2")
        self.assertEqual(issue.field_path, "tasks[1].depends_on[1].task_id")
        self.assertTrue(report.blocked)

    def test_unknown_prerequisite_is_rejected(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][1]["depends_on"].append(
            {"task_id": "C999", "reason": "错误样例：前置任务不存在"}
        )
        report = validate_task_plan(broken, spec=spec, template=template)
        issue = _find(report, codes.DEPENDENCY_UNKNOWN)
        self.assertEqual(issue.object_id, "C2")
        self.assertEqual(issue.field_path, "tasks[1].depends_on[1].task_id")
        self.assertEqual(issue.related_ids, ["C999"])
        self.assertTrue(report.blocked)


class V06ArtifactTest(unittest.TestCase):
    def test_unresolved_task_output_is_rejected(self) -> None:
        spec, _, template = _chat_inputs()
        plan = _load("examples/contracts/invalid/unresolved-artifact/task-plan.json")
        report = validate_task_plan(plan, spec=spec, template=template)
        self.assertTrue(report.blocked)
        issue = _find(report, codes.ARTIFACT_OUTPUT_UNKNOWN)
        self.assertEqual(issue.object_id, "C6")
        self.assertEqual(issue.field_path, "tasks[5].inputs[1].ref")
        self.assertEqual(issue.related_ids, ["C2", "O-API"])
        self.assertIn("O-API", issue.message)

    def test_consumer_must_declare_producer_dependency(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][3]["depends_on"] = [
            {"task_id": "C1", "reason": "错误样例：故意漏掉生产任务 C2"}
        ]
        report = validate_task_plan(broken, spec=spec, template=template)
        issue = _find(report, codes.ARTIFACT_PRODUCER_DEPENDENCY_MISSING)
        self.assertEqual(issue.object_id, "C4")
        self.assertEqual(issue.related_ids, ["C2"])
        self.assertEqual(issue.field_path, "tasks[3].depends_on")

    def test_design_input_type_must_be_design_contract(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["inputs"][0]["type"] = "implementation"
        report = validate_task_plan(broken, spec=spec, template=template)
        issue = _find(report, codes.ARTIFACT_TYPE_MISMATCH)
        self.assertEqual(issue.object_id, "C1")
        self.assertEqual(issue.field_path, "tasks[0].inputs[0].type")


class StructureCheckTest(unittest.TestCase):
    def test_missing_field_reports_exact_path(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        del broken["tasks"][0]["title"]
        report = validate_task_plan(broken, spec=spec, template=template)
        self.assertTrue(report.blocked)
        self.assertIn((codes.FIELD_REQUIRED, "tasks[0].title"), _paths(report))

    def test_unknown_field_is_rejected(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["unexpected"] = 1
        report = validate_task_plan(broken, spec=spec, template=template)
        self.assertIn((codes.FIELD_UNKNOWN, "tasks[0].unexpected"), _paths(report))

    def test_wrong_type_reports_expected_type(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["priority"] = "high"
        report = validate_task_plan(broken, spec=spec, template=template)
        issue = _find(report, codes.FIELD_TYPE)
        self.assertEqual(issue.field_path, "tasks[0].priority")
        self.assertIn("priority", issue.message)

    def test_empty_acceptance_is_rejected(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["acceptance"] = []
        report = validate_task_plan(broken, spec=spec, template=template)
        self.assertIn((codes.FIELD_EMPTY, "tasks[0].acceptance"), _paths(report))

    def test_invalid_enum_value_is_reported(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["kind"] = "unknown"
        broken["generation_mode"] = "magic"
        report = validate_task_plan(broken, spec=spec, template=template)
        paths = _paths(report)
        self.assertIn((codes.FIELD_CHOICE, "tasks[0].kind"), paths)
        self.assertIn((codes.FIELD_CHOICE, "generation_mode"), paths)


class ReferenceCheckTest(unittest.TestCase):
    def test_duplicate_task_id(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][1]["id"] = "C1"
        report = validate_task_plan(broken, spec=spec, template=template)
        self.assertIn((codes.TASK_ID_DUPLICATE, "tasks[1].id"), _paths(report))
        self.assertTrue(report.blocked)

    def test_unknown_requirement_reference(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["requirement_ids"] = ["R999"]
        report = validate_task_plan(broken, spec=spec, template=template)
        self.assertIn(
            (codes.REF_REQUIREMENT_UNKNOWN, "tasks[0].requirement_ids[0]"), _paths(report)
        )
        self.assertTrue(report.blocked)

    def test_role_and_stage_must_follow_template(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["tasks"][0]["role"] = "designer"
        broken["tasks"][0]["stage"] = "release"
        report = validate_task_plan(broken, spec=spec, template=template)
        paths = _paths(report)
        self.assertIn((codes.TASK_ROLE_NOT_ALLOWED, "tasks[0].role"), paths)
        self.assertIn((codes.TASK_STAGE_NOT_IN_TEMPLATE, "tasks[0].stage"), paths)

    def test_spec_and_template_version_mismatch(self) -> None:
        spec, plan, template = _chat_inputs()
        broken = _copy(plan)
        broken["spec_ref"]["version"] = 2
        broken["template_ref"]["version"] = 2
        report = validate_task_plan(broken, spec=spec, template=template)
        paths = _paths(report)
        self.assertIn((codes.REF_VERSION_MISMATCH, "spec_ref.version"), paths)
        self.assertIn((codes.REF_VERSION_MISMATCH, "template_ref.version"), paths)


class RevisionCheckTest(unittest.TestCase):
    def test_stale_plan_revision_is_rejected(self) -> None:
        spec, plan, template = _chat_inputs()
        report = validate_task_plan(plan, spec=spec, template=template, expected_revision=2)
        issue = _find(report, codes.REVISION_MISMATCH)
        self.assertEqual(issue.field_path, "revision")
        self.assertIn("409", issue.message)
        self.assertTrue(report.blocked)

    def test_stale_spec_version_is_rejected(self) -> None:
        spec = _load("examples/contracts/chat/design-spec.json")
        report = validate_design_spec(spec, expected_version=2)
        issue = _find(report, codes.REVISION_MISMATCH)
        self.assertEqual(issue.field_path, "version")
        self.assertTrue(report.blocked)


class ExecutionPlanCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec, self.plan, self.template = _chat_inputs()
        self.execution_plan = _load("examples/contracts/chat/execution-plan.json")
        self.report = _load("examples/contracts/chat/validation-report.json")

    def _validate(self, execution_plan: dict[str, Any]) -> Any:
        return validate_execution_plan(
            execution_plan,
            task_plan=self.plan,
            validation_report=self.report,
            spec=self.spec,
            template=self.template,
        )

    def test_reversed_edge_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        edge = broken["edges"][0]
        broken["edges"][0] = {"from_task": edge["to_task"], "to_task": edge["from_task"]}
        report = self._validate(broken)
        issue = _find(report, codes.EXEC_EDGE_DIRECTION_REVERSED)
        self.assertTrue(issue.field_path.startswith("edges[0]"))
        self.assertIn("方向", issue.message)
        self.assertTrue(report.blocked)

    def test_batch_over_global_capacity_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["batches"][1].append("C1")
        report = self._validate(broken)
        issue = _find(report, codes.EXEC_BATCH_OVER_CAPACITY)
        self.assertEqual(issue.field_path, "batches[1]")
        self.assertEqual(issue.related_ids or [], issue.related_ids)
        self.assertTrue(report.blocked)

    def test_batch_role_capacity_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["batches"][0].append("C2")  # C1、C2 同为 backend，容量 1
        broken["bindings"] = [
            binding for binding in broken["bindings"] if binding["task_id"] not in {"C2"}
        ]
        broken["nodes"] = [node for node in broken["nodes"] if node["task_id"] != "C2"]
        report = self._validate(broken)
        found = {code for code, _ in _paths(report)}
        self.assertIn(codes.EXEC_BATCH_ROLE_CAPACITY, found)

    def test_stale_validation_digest_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["validation_ref"]["report_digest"] = "0" * 64
        report = self._validate(broken)
        issue = _find(report, codes.EXEC_VALIDATION_STALE)
        self.assertEqual(issue.field_path, "validation_ref.report_digest")
        self.assertTrue(report.blocked)

    def test_blocked_validation_report_blocks_compile(self) -> None:
        cycle_report = _load(
            "examples/contracts/invalid/cycle/validation-report.json"
        )
        report = validate_execution_plan(
            self.execution_plan,
            task_plan=self.plan,
            validation_report=cycle_report,
            spec=self.spec,
            template=self.template,
        )
        found = {code for code, _ in _paths(report)}
        self.assertIn(codes.EXEC_VALIDATION_BLOCKED, found)
        self.assertTrue(report.blocked)

    def test_outdated_source_digest_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["config_digest"] = "0" * 64
        report = self._validate(broken)
        issue = _find(report, codes.EXEC_SOURCE_DIGEST_MISMATCH)
        self.assertEqual(issue.field_path, "config_digest")
        self.assertTrue(report.blocked)

    def test_missing_node_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["nodes"] = [node for node in broken["nodes"] if node["task_id"] != "C8"]
        report = self._validate(broken)
        issue = _find(report, codes.EXEC_NODE_MISSING)
        self.assertEqual(issue.object_id, "C8")

    def test_plan_ref_mismatch_is_reported(self) -> None:
        broken = _copy(self.execution_plan)
        broken["plan_ref"]["revision"] = 9
        report = self._validate(broken)
        self.assertIn(codes.EXEC_PLAN_REF_MISMATCH, {code for code, _ in _paths(report)})


class DeterminismTest(unittest.TestCase):
    def test_validation_is_deterministic(self) -> None:
        spec, plan, template = _chat_inputs()
        first = to_jsonable(validate_task_plan(plan, spec=spec, template=template))
        second = to_jsonable(validate_task_plan(_copy(plan), spec=spec, template=template))
        self.assertEqual(first, second)
        self.assertEqual(content_digest(first), content_digest(second))

    def test_reports_use_stable_issue_order(self) -> None:
        spec, _, template = _chat_inputs()
        plan = _load("examples/contracts/invalid/missing-verification/task-plan.json")
        first = [issue.code for issue in validate_task_plan(plan, spec=spec, template=template).issues]
        second = [issue.code for issue in validate_task_plan(plan, spec=spec, template=template).issues]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
