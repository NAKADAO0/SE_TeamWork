"""成员 4（确定性编排）测试：复现契约样例、确定性、角色容量、等待原因与准入规则。

覆盖设计文档第 8.3 节的编排相关验收：

- V07 相同输入重复编排：节点、边、批次、绑定一致；
- V08 改变容量：批次相应变化、无超额分配、依赖边保持不变；
- V05 的防御面：环/缺失前置/自依赖在排批前被拒绝并给出闭合路径；
- V10 的编排侧：陈旧校验报告拒绝编排。

测试同时用成员 3 的 ``validate_execution_plan`` 交叉核对输出（“3 复核计算”的自动化版本）。
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.orchestration import (
    BLOCKED,
    CONFIG_INVALID,
    DEPENDENCY_CYCLE,
    DEPENDENCY_SELF,
    DEPENDENCY_UNKNOWN,
    PLAN_INVALID,
    ROLE_CAPACITY_MISSING,
    TASK_ID_DUPLICATE,
    VALIDATION_BLOCKED,
    VALIDATION_MISSING,
    VALIDATION_STALE,
    WAIT_DEPENDENCY,
    WAIT_GLOBAL_CAPACITY,
    WAIT_ROLE_CAPACITY,
    OrchestrationError,
    RunConfig,
    as_compiler,
    build_graph,
    compile_batches,
    compile_plan,
    explain_batches,
    schedule,
    summary,
)
from backend.app.validation.validator import validate_execution_plan
from backend.tools.build_orchestration_fixtures import build_documents, check_all

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "examples" / "contracts"
ORCHESTRATION_DIR = REPO_ROOT / "examples" / "orchestration"
TEMPLATE_PATH = REPO_ROOT / "templates" / "default-workflow.json"
WAIT_CODES = (WAIT_DEPENDENCY, WAIT_GLOBAL_CAPACITY, WAIT_ROLE_CAPACITY)


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return json.load(handle)


TEMPLATE = _load(TEMPLATE_PATH)
CHAT_PLAN = _load(CONTRACTS_DIR / "chat" / "task-plan.json")
CHAT_SPEC = _load(CONTRACTS_DIR / "chat" / "design-spec.json")
CHAT_REPORT = _load(CONTRACTS_DIR / "chat" / "validation-report.json")
CHAT_CURATED = _load(CONTRACTS_DIR / "chat" / "execution-plan.json")
COMMUNITY_PLAN = _load(CONTRACTS_DIR / "community" / "task-plan.json")
COMMUNITY_SPEC = _load(CONTRACTS_DIR / "community" / "design-spec.json")
COMMUNITY_REPORT = _load(CONTRACTS_DIR / "community" / "validation-report.json")


def _compile_chat(**overrides: Any):
    arguments: dict[str, Any] = {
        "spec": CHAT_SPEC,
        "template": TEMPLATE,
        "validation_report": CHAT_REPORT,
    }
    arguments.update(overrides)
    return compile_plan(CHAT_PLAN, **arguments)


def _plan_copy() -> dict[str, Any]:
    return copy.deepcopy(CHAT_PLAN)


def _synthetic_plan(tasks: list[dict[str, Any]], *, max_concurrency: int = 2, roles: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "plan_id": "PLAN-SYNTH",
        "revision": 1,
        "spec_ref": {"spec_id": "SPEC-SYNTH", "version": 1},
        "template_ref": {"template_id": "TPL-SYNTH", "version": 1},
        "config": {
            "max_concurrency": max_concurrency,
            "role_capacity": roles or {"backend": 1, "frontend": 1, "qa": 1},
            "ordering": ["priority", "task_id"],
        },
        "generation_mode": "curated_example",
        "tasks": tasks,
    }


SYNTH_SPEC = {"schema_version": "1.0", "spec_id": "SPEC-SYNTH", "version": 1}
SYNTH_TEMPLATE = {"schema_version": "1.0", "template_id": "TPL-SYNTH", "version": 1}
SYNTH_REPORT = {
    "schema_version": "1.0",
    "target": {"kind": "task_plan", "plan_id": "PLAN-SYNTH", "revision": 1},
    "content_digest": "0" * 64,
    "blocked": False,
    "summary": {"errors": 0, "warnings": 0},
    "issues": [],
}


def _task(task_id: str, role: str, depends_on: list[str] | None = None, *, stage: str = "implement", priority: int = 1) -> dict[str, Any]:
    return {
        "id": task_id,
        "role": role,
        "stage": stage,
        "priority": priority,
        "depends_on": [{"task_id": dependency, "reason": f"依赖 {dependency}"} for dependency in depends_on or []],
    }


class ChatParityTest(unittest.TestCase):
    """与成员 3 的 curated 执行计划逐字段一致，并通过其执行计划校验器。"""

    def test_matches_member3_curated_execution_plan(self) -> None:
        result = _compile_chat(execution_plan_id="EXEC-CHAT", verify=True)
        self.assertEqual(result.document(), CHAT_CURATED)

    def test_counts_and_status_match_design(self) -> None:
        result = _compile_chat(execution_plan_id="EXEC-CHAT")
        plan = result.execution_plan
        self.assertEqual(len(plan.nodes), 8)
        self.assertEqual(len(plan.edges), 11)
        self.assertEqual(len(plan.batches), 6)
        self.assertEqual(len(plan.topological_levels), 6)
        self.assertEqual(
            [list(batch) for batch in plan.batches],
            [["C1"], ["C2", "C3"], ["C4", "C6"], ["C5"], ["C7"], ["C8"]],
        )
        self.assertIn("8 节点", summary(result))
        self.assertIn("6 批", summary(result))

    def test_passes_member3_execution_plan_validator(self) -> None:
        document = _compile_chat(execution_plan_id="EXEC-CHAT").document()
        report = validate_execution_plan(
            document,
            task_plan=CHAT_PLAN,
            validation_report=CHAT_REPORT,
            spec=CHAT_SPEC,
            template=TEMPLATE,
        )
        self.assertEqual([issue.code for issue in report.issues], [])
        self.assertFalse(report.blocked)

    def test_every_task_scheduled_exactly_once(self) -> None:
        plan = _compile_chat().execution_plan
        scheduled = [task_id for batch in plan.batches for task_id in batch]
        self.assertEqual(sorted(scheduled), sorted(task["id"] for task in CHAT_PLAN["tasks"]))
        self.assertEqual(len(scheduled), len(set(scheduled)))

    def test_edges_follow_depends_on_direction(self) -> None:
        plan = _compile_chat().execution_plan
        expected = {
            (dependency["task_id"], task["id"])
            for task in CHAT_PLAN["tasks"]
            for dependency in task["depends_on"]
        }
        self.assertEqual({(edge.from_task, edge.to_task) for edge in plan.edges}, expected)


class DeterminismTest(unittest.TestCase):
    """V07：相同输入产生相同结果。"""

    def test_repeated_compiles_are_identical(self) -> None:
        first = _compile_chat()
        second = _compile_chat()
        self.assertEqual(first.document(), second.document())
        self.assertEqual(first.explanation_document(), second.explanation_document())
        # 缺省执行计划编号也由内容推导，重复编排不会产生不同编号。
        self.assertEqual(first.execution_plan.execution_plan_id, second.execution_plan.execution_plan_id)

    def test_task_declaration_order_does_not_change_schedule(self) -> None:
        shuffled = _plan_copy()
        shuffled["tasks"] = list(reversed(shuffled["tasks"]))
        base = _compile_chat().document()
        other = compile_plan(
            shuffled, spec=CHAT_SPEC, template=TEMPLATE, validation_report=CHAT_REPORT
        ).document()
        self.assertEqual(base["batches"], other["batches"])
        self.assertEqual(base["topological_levels"], other["topological_levels"])
        self.assertEqual(base["bindings"], other["bindings"])
        self.assertEqual(
            {(edge["from_task"], edge["to_task"]) for edge in base["edges"]},
            {(edge["from_task"], edge["to_task"]) for edge in other["edges"]},
        )

    def test_ordering_without_task_id_falls_back_to_task_id(self) -> None:
        plan = _plan_copy()
        plan["config"]["ordering"] = ["priority"]
        result = compile_plan(
            plan, spec=CHAT_SPEC, template=TEMPLATE, validation_report=CHAT_REPORT
        )
        self.assertEqual(
            [list(batch) for batch in result.execution_plan.batches],
            [["C1"], ["C2", "C3"], ["C4", "C6"], ["C5"], ["C7"], ["C8"]],
        )
        self.assertIn("task_id", result.explanation["config"]["ordering"])

    def test_fifty_task_graph_is_deterministic_and_valid(self) -> None:
        tasks: list[dict[str, Any]] = []
        for chain in range(10):
            previous: str | None = None
            for index in range(5):
                task_id = f"T{chain}-{index}"
                role = "backend" if index < 3 else "qa"
                stage = "implement" if index < 3 else "verify"
                tasks.append(
                    _task(task_id, role, [previous] if previous else [], stage=stage)
                )
                previous = task_id
        plan = _synthetic_plan(tasks, max_concurrency=4, roles={"backend": 2, "qa": 2})
        first = compile_plan(
            plan, spec=SYNTH_SPEC, template=SYNTH_TEMPLATE, validation_report=SYNTH_REPORT
        )
        second = compile_plan(
            plan, spec=SYNTH_SPEC, template=SYNTH_TEMPLATE, validation_report=SYNTH_REPORT
        )
        self.assertEqual(first.document(), second.document())

        execution = first.execution_plan
        scheduled = [task_id for batch in execution.batches for task_id in batch]
        self.assertEqual(len(scheduled), 50)
        self.assertEqual(sorted(scheduled), sorted(task["id"] for task in tasks))
        for batch in execution.batches:
            self.assertLessEqual(len(batch), 4)
            self.assertLessEqual(sum(1 for task_id in batch if task_id.endswith(("3", "4"))), 4)
        position = {task_id: index for index, task_id in enumerate(scheduled)}
        for task in tasks:
            for dependency in task["depends_on"]:
                self.assertLess(position[dependency["task_id"]], position[task["id"]])


class CapacityTest(unittest.TestCase):
    """V08：容量变化只改变资源安排，不改变依赖边。"""

    def _variants(self) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        base = compile_plan(
            COMMUNITY_PLAN,
            spec=COMMUNITY_SPEC,
            template=TEMPLATE,
            validation_report=COMMUNITY_REPORT,
        )
        lowered_plan = _load(ORCHESTRATION_DIR / "community-qa1" / "task-plan.json")
        lowered_report = _load(ORCHESTRATION_DIR / "community-qa1" / "validation-report.json")
        lowered = compile_plan(
            lowered_plan,
            spec=COMMUNITY_SPEC,
            template=TEMPLATE,
            validation_report=lowered_report,
        )
        return base, lowered_plan, lowered

    def test_lowering_qa_capacity_changes_batches_only(self) -> None:
        base, _, lowered = self._variants()
        self.assertEqual(
            [list(batch) for batch in base.execution_plan.batches],
            [["N1"], ["N2", "N3"], ["N4", "N5"], ["N6", "N7"], ["N8"]],
        )
        self.assertEqual(
            [list(batch) for batch in lowered.execution_plan.batches],
            [["N1"], ["N2", "N3"], ["N4", "N5"], ["N6"], ["N7"], ["N8"]],
        )
        self.assertGreater(len(lowered.execution_plan.batches), len(base.execution_plan.batches))
        self.assertEqual(
            {(edge.from_task, edge.to_task) for edge in base.execution_plan.edges},
            {(edge.from_task, edge.to_task) for edge in lowered.execution_plan.edges},
        )

    def test_no_batch_exceeds_global_or_role_capacity(self) -> None:
        base, lowered_plan, lowered = self._variants()
        for result, plan in ((base, COMMUNITY_PLAN), (lowered, lowered_plan)):
            config = plan["config"]
            document = result.document()
            roles = {node["task_id"]: node["role"] for node in document["nodes"]}
            for batch in document["batches"]:
                self.assertLessEqual(len(batch), config["max_concurrency"])
                for role, capacity in config["role_capacity"].items():
                    count = sum(1 for task_id in batch if roles[task_id] == role)
                    self.assertLessEqual(count, capacity)

    def test_bindings_use_lowest_free_slots(self) -> None:
        plan = _compile_chat().document()
        roles = {node["task_id"]: node["role"] for node in plan["nodes"]}
        bindings = {binding["task_id"]: binding for binding in plan["bindings"]}
        for batch in plan["batches"]:
            used_global: set[int] = set()
            used_role: dict[str, set[int]] = {}
            for task_id in batch:
                binding = bindings[task_id]
                self.assertNotIn(binding["global_slot"], used_global)
                used_global.add(binding["global_slot"])
                role = roles[task_id]
                used_role.setdefault(role, set())
                self.assertNotIn(binding["role_slot"], used_role[role])
                used_role[role].add(binding["role_slot"])
            self.assertEqual(used_global, set(range(1, len(batch) + 1)))
            for slots in used_role.values():
                self.assertEqual(slots, set(range(1, len(slots) + 1)))


class WaitReasonTest(unittest.TestCase):
    """等待原因：依赖等待与资源等待分开，且每条未安排任务都有解释。"""

    def test_every_unscheduled_task_is_explained(self) -> None:
        explanation = _compile_chat().explanation_document()
        seen: set[str] = set()
        for batch in explanation["batches"]:
            seen.update(batch["tasks"])
            seen.update(item["task_id"] for item in batch["waiting"])
        self.assertEqual(seen, {task["id"] for task in CHAT_PLAN["tasks"]})
        for batch in explanation["batches"]:
            for item in batch["waiting"]:
                self.assertIn(item["code"], WAIT_CODES)
                self.assertTrue(item["message"])
                self.assertIn("task_id", item)

    def test_role_capacity_wait_is_reported_as_resource_wait(self) -> None:
        tasks = [_task("A", "backend"), _task("B", "backend"), _task("VA", "qa", ["A"]), _task("VB", "qa", ["B"])]
        plan = _synthetic_plan(tasks, max_concurrency=4, roles={"backend": 2, "qa": 1})
        result = compile_plan(
            plan, spec=SYNTH_SPEC, template=SYNTH_TEMPLATE, validation_report=SYNTH_REPORT
        )
        explanation = result.explanation_document()
        self.assertEqual(explanation["batches"][0]["tasks"], ["A", "B"])
        waiting = {item["task_id"]: item for item in explanation["batches"][1]["waiting"]}
        self.assertEqual(waiting["VB"]["code"], WAIT_ROLE_CAPACITY)
        self.assertEqual(waiting["VB"]["role"], "qa")
        self.assertEqual(waiting["VB"]["capacity"], 1)
        self.assertIn("容量已满", waiting["VB"]["message"])
        self.assertNotIn("等待前置任务", waiting["VB"]["message"])

    def test_dependency_wait_names_prerequisite_and_reason(self) -> None:
        explanation = _compile_chat().explanation_document()
        first_batch = {item["task_id"]: item for item in explanation["batches"][0]["waiting"]}
        c4 = first_batch["C4"]
        self.assertEqual(c4["code"], WAIT_DEPENDENCY)
        self.assertEqual(c4["blocking_task_ids"], ["C2"])
        self.assertIn("C2", c4["message"])
        self.assertIn("复用消息查询服务与鉴权", c4["message"])

    def test_global_capacity_wait_mentions_batch(self) -> None:
        explanation = compile_plan(
            COMMUNITY_PLAN,
            spec=COMMUNITY_SPEC,
            template=TEMPLATE,
            validation_report=COMMUNITY_REPORT,
        ).explanation_document()
        second_batch = {item["task_id"]: item for item in explanation["batches"][1]["waiting"]}
        self.assertEqual(second_batch["N4"]["code"], WAIT_GLOBAL_CAPACITY)
        self.assertIn("全局并发已满", second_batch["N4"]["message"])

    def test_explanation_matches_execution_plan(self) -> None:
        result = _compile_chat()
        document = result.document()
        explanation = result.explanation_document()
        self.assertEqual(explanation["execution_plan_id"], document["execution_plan_id"])
        self.assertEqual(explanation["plan_ref"], document["plan_ref"])
        self.assertEqual(
            [batch["tasks"] for batch in explanation["batches"]],
            document["batches"],
        )
        self.assertFalse(explanation["blocked"])


class AdmissionTest(unittest.TestCase):
    """准入：先有同一修订的通过报告，才允许编排。"""

    def test_missing_validation_report_is_rejected(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(CHAT_PLAN, spec=CHAT_SPEC, template=TEMPLATE, validation_report=None)
        self.assertEqual(raised.exception.code, VALIDATION_MISSING)
        self.assertEqual(raised.exception.status, 422)

    def test_blocked_report_is_rejected_with_issues(self) -> None:
        cycle_plan = _load(CONTRACTS_DIR / "invalid" / "cycle" / "task-plan.json")
        cycle_report = _load(CONTRACTS_DIR / "invalid" / "cycle" / "validation-report.json")
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(cycle_plan, spec=CHAT_SPEC, template=TEMPLATE, validation_report=cycle_report)
        error = raised.exception
        self.assertEqual(error.code, VALIDATION_BLOCKED)
        codes = {issue["code"] for issue in error.details["issues"]}
        self.assertIn("DEPENDENCY_CYCLE", codes)

    def test_stale_report_revision_is_rejected(self) -> None:
        stale = copy.deepcopy(CHAT_REPORT)
        stale["target"]["revision"] = 2
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(CHAT_PLAN, spec=CHAT_SPEC, template=TEMPLATE, validation_report=stale)
        self.assertEqual(raised.exception.code, VALIDATION_STALE)
        self.assertEqual(raised.exception.status, 409)

    def test_report_bound_to_another_plan_is_rejected(self) -> None:
        other = copy.deepcopy(CHAT_REPORT)
        other["target"]["plan_id"] = "PLAN-OTHER"
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(CHAT_PLAN, spec=CHAT_SPEC, template=TEMPLATE, validation_report=other)
        self.assertEqual(raised.exception.code, VALIDATION_STALE)

    def test_spec_version_mismatch_is_rejected(self) -> None:
        newer_spec = copy.deepcopy(CHAT_SPEC)
        newer_spec["version"] = 2
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(CHAT_PLAN, spec=newer_spec, template=TEMPLATE, validation_report=CHAT_REPORT)
        self.assertEqual(raised.exception.code, VALIDATION_STALE)

    def test_plan_without_required_fields_is_rejected(self) -> None:
        broken = _plan_copy()
        del broken["config"]
        with self.assertRaises(OrchestrationError) as raised:
            compile_plan(broken, spec=CHAT_SPEC, template=TEMPLATE, validation_report=CHAT_REPORT)
        self.assertEqual(raised.exception.code, PLAN_INVALID)

    def test_error_object_shape(self) -> None:
        error = OrchestrationError(
            VALIDATION_STALE, "报告陈旧", field_path="validation_ref", details={"plan_id": "P"}
        )
        document = error.as_dict(request_id="REQ-1")
        self.assertEqual(document["code"], VALIDATION_STALE)
        self.assertEqual(document["request_id"], "REQ-1")
        self.assertEqual(document["details"]["field_path"], "validation_ref")
        self.assertIsInstance(error, ValueError)


class ConfigTest(unittest.TestCase):
    """配置规则：容量必须为正整数，角色必须配置，排序键必须可复现。"""

    def _compile_with(self, **config: Any):
        plan = _plan_copy()
        plan["config"].update(config)
        return compile_plan(plan, spec=CHAT_SPEC, template=TEMPLATE, validation_report=CHAT_REPORT)

    def test_zero_concurrency_is_rejected(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            self._compile_with(max_concurrency=0)
        self.assertEqual(raised.exception.code, CONFIG_INVALID)

    def test_zero_role_capacity_is_rejected(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            self._compile_with(role_capacity={"backend": 0, "frontend": 1, "qa": 1})
        self.assertEqual(raised.exception.code, CONFIG_INVALID)

    def test_unknown_ordering_key_is_rejected(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            self._compile_with(ordering=["priority", "stage", "task_id"])
        self.assertEqual(raised.exception.code, CONFIG_INVALID)

    def test_missing_role_capacity_is_rejected(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            self._compile_with(role_capacity={"backend": 1, "frontend": 1})
        self.assertEqual(raised.exception.code, ROLE_CAPACITY_MISSING)
        self.assertEqual(raised.exception.details["role"], "qa")

    def test_config_digest_follows_plan_config(self) -> None:
        from backend.app.contracts import content_digest

        result = _compile_chat()
        self.assertEqual(result.execution_plan.config_digest, content_digest(CHAT_PLAN["config"]))
        changed = _plan_copy()
        changed["config"]["max_concurrency"] = 3
        other = compile_plan(
            changed, spec=CHAT_SPEC, template=TEMPLATE, validation_report=CHAT_REPORT
        )
        self.assertNotEqual(other.execution_plan.config_digest, result.execution_plan.config_digest)


class GraphGuardTest(unittest.TestCase):
    """防御式图检查：进入排批前就必须能终止。"""

    def test_duplicate_task_id(self) -> None:
        tasks = [_task("A", "backend"), _task("A", "qa")]
        with self.assertRaises(OrchestrationError) as raised:
            build_graph(_synthetic_plan(tasks))
        self.assertEqual(raised.exception.code, TASK_ID_DUPLICATE)

    def test_unknown_dependency(self) -> None:
        tasks = [_task("A", "backend", ["NOPE"])]
        with self.assertRaises(OrchestrationError) as raised:
            build_graph(_synthetic_plan(tasks))
        self.assertEqual(raised.exception.code, DEPENDENCY_UNKNOWN)

    def test_self_dependency(self) -> None:
        tasks = [_task("A", "backend", ["A"])]
        with self.assertRaises(OrchestrationError) as raised:
            build_graph(_synthetic_plan(tasks))
        self.assertEqual(raised.exception.code, DEPENDENCY_SELF)

    def test_cycle_reports_closed_path(self) -> None:
        cycle_plan = _load(CONTRACTS_DIR / "invalid" / "cycle" / "task-plan.json")
        with self.assertRaises(OrchestrationError) as raised:
            build_graph(cycle_plan)
        self.assertEqual(raised.exception.code, DEPENDENCY_CYCLE)
        self.assertIn("C5 → C7 → C5", raised.exception.message)
        self.assertEqual(raised.exception.details["cycle"], ["C5", "C7"])

    def test_scheduler_blocks_instead_of_looping(self) -> None:
        graph = build_graph(_synthetic_plan([_task("A", "backend")]))
        broken = RunConfig(max_concurrency=0, role_capacity={"backend": 1}, ordering=("priority", "task_id"))
        with self.assertRaises(OrchestrationError) as raised:
            schedule(graph, broken)
        self.assertEqual(raised.exception.code, BLOCKED)
        self.assertEqual(raised.exception.details["unscheduled"], ["A"])

    def test_levels_use_longest_path(self) -> None:
        graph = build_graph(CHAT_PLAN)
        level_of = graph.level_of()
        self.assertEqual(level_of["C1"], 0)
        self.assertEqual(level_of["C4"], 2)
        self.assertEqual(level_of["C8"], 5)


class LegacyAdapterTest(unittest.TestCase):
    """兼容成员 5 的 compiler(plan) -> 批次 注入点。"""

    def test_demo_plan_batches_match_demo_rules(self) -> None:
        demo_plan = _load(REPO_ROOT / "examples" / "chat-plan.json")
        self.assertEqual(
            compile_batches(demo_plan),
            [["C1"], ["C2", "C3"], ["C4", "C6"], ["C5"], ["C7"], ["C8"]],
        )

    def test_decompose_style_tasks_match_demo_test(self) -> None:
        tasks = [
            {"id": "R1-I", "depends_on": []},
            {"id": "R2-I", "depends_on": []},
            {"id": "R1-V", "depends_on": ["R1-I"]},
            {"id": "R2-V", "depends_on": ["R2-I"]},
        ]
        self.assertEqual(
            compile_batches(tasks, config={"max_concurrency": 2, "role_capacity": {"default": 2}}),
            [["R1-I", "R2-I"], ["R1-V", "R2-V"]],
        )
        self.assertEqual(
            len(compile_batches(tasks, config={"max_concurrency": 1, "role_capacity": {"default": 1}})),
            4,
        )

    def test_taskplan_shape_matches_compile_plan(self) -> None:
        expected = _compile_chat().document()["batches"]
        self.assertEqual(compile_batches(CHAT_PLAN), expected)

    def test_as_compiler_is_plain_callable(self) -> None:
        compiler = as_compiler()
        demo_plan = _load(REPO_ROOT / "examples" / "chat-plan.json")
        self.assertEqual(compiler(demo_plan), compile_batches(demo_plan))
        # 显式配置也可以，但必须覆盖计划里出现的全部角色。
        explicit = as_compiler(
            {
                "max_concurrency": 2,
                "role_capacity": {"backend": 1, "frontend": 1, "qa": 1},
            }
        )
        self.assertEqual(explicit(demo_plan), compile_batches(demo_plan))

    def test_explain_batches_returns_wait_reasons(self) -> None:
        demo_plan = _load(REPO_ROOT / "examples" / "chat-plan.json")
        explanation = explain_batches(demo_plan)
        self.assertFalse(explanation["blocked"])
        self.assertEqual(explanation["plan_ref"]["plan_id"], "PLAN-ADAPTER")
        self.assertEqual([batch["tasks"] for batch in explanation["batches"]][0], ["C1"])
        codes = {
            item["code"]
            for batch in explanation["batches"]
            for item in batch["waiting"]
        }
        self.assertTrue(codes)
        self.assertTrue(codes <= set(WAIT_CODES))

    def test_invalid_demo_plan_raises_domain_error(self) -> None:
        with self.assertRaises(OrchestrationError) as raised:
            compile_batches([{"id": "A", "depends_on": ["MISSING"]}])
        self.assertEqual(raised.exception.code, DEPENDENCY_UNKNOWN)
        with self.assertRaises(OrchestrationError) as raised:
            compile_batches([{"id": "A", "depends_on": []}], config={"max_concurrency": 0})
        self.assertEqual(raised.exception.code, CONFIG_INVALID)


class FixtureFreshnessTest(unittest.TestCase):
    """examples/orchestration 下的派生 fixture 必须与算法输出一致。"""

    def test_orchestration_fixtures_are_fresh(self) -> None:
        self.assertEqual(check_all(build_documents()), [])

    def test_fixture_files_exist(self) -> None:
        expected = [
            "chat/explanation.json",
            "community/execution-plan.json",
            "community/explanation.json",
            "community-qa1/task-plan.json",
            "community-qa1/validation-report.json",
            "community-qa1/execution-plan.json",
            "community-qa1/explanation.json",
        ]
        for relative in expected:
            self.assertTrue((ORCHESTRATION_DIR / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
