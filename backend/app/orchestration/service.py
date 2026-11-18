"""编排领域服务：校验准入 → 任务图 → 拓扑层 → 确定性批次、角色槽位与等待原因。

对成员 5（API / 存储 / 版本导出）：
    调用 :func:`compile_plan` 得到 :class:`OrchestrationResult`；
    ``result.document()`` 是 ExecutionPlan 契约对象，可存为 ``artifacts(kind="execution")``；
    ``result.explanation_document()`` 是等待原因说明，可另存为 ``kind="explanation"``。
    :class:`~backend.app.orchestration.errors.OrchestrationError` 的 ``code/status``
    可直接映射为统一错误对象与 HTTP 状态。

对成员 6（可视化 / 集成）：
    ``nodes``、``edges``、``topological_levels``、``batches``、``bindings`` 全在
    ExecutionPlan 内；图只渲染这些领域结果，坐标不参与调度。等待原因不改动批次，
    只解释“为什么这个任务不在本批”。

对成员 3（契约与校验）：
    入口先核对校验报告的 ``target`` 与计划 ``plan_ref`` 一致、``blocked=false``，
    再计算；不重复实现引用、覆盖、产物等规则。``verify=True`` 时额外调用
    ``validate_execution_plan`` 做交叉核对（集成测试用，默认关闭）。

可复现性（V07）：相同 TaskPlan、模板与配置产生完全相同的文档——节点按任务声明顺序、
边按 depends_on 归一化顺序、批次按“priority 升序、task_id 升序”、绑定按批内槽位；
文档不含时间戳，``execution_plan_id`` 缺省时也由内容摘要推导。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ..contracts.models import (
    SCHEMA_VERSION,
    Binding,
    Edge,
    ExecutionNode,
    ExecutionPlan,
    PlanRef,
    ValidationRef,
    content_digest,
    to_jsonable,
)
from .batches import RunConfig, schedule
from .errors import (
    BLOCKED,
    CONFIG_INVALID,
    PLAN_INVALID,
    SELF_CHECK_FAILED,
    VALIDATION_BLOCKED,
    VALIDATION_MISSING,
    VALIDATION_STALE,
    OrchestrationError,
)
from .graph import TaskGraph, build_graph

REQUIRED_PLAN_FIELDS = ("plan_id", "revision", "config", "tasks")


def _plain(value: Any) -> Any:
    """契约 dataclass → 普通 JSON 结构；普通结构原样返回。"""
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, str, int, float)) or value is None:
        return value
    return to_jsonable(value)


def _plan_document(plan: Any) -> dict[str, Any]:
    document = _plain(plan)
    if not isinstance(document, Mapping):
        raise OrchestrationError(PLAN_INVALID, "任务计划必须是 JSON 对象")
    for field in REQUIRED_PLAN_FIELDS:
        if field not in document:
            raise OrchestrationError(
                PLAN_INVALID,
                f"任务计划缺少字段 {field}",
                field_path=field,
            )
    plan_id = document.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise OrchestrationError(PLAN_INVALID, "plan_id 必须是非空字符串", field_path="plan_id")
    revision = document.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise OrchestrationError(
            PLAN_INVALID, "revision 必须是正整数", field_path="revision"
        )
    return dict(document)


def _check_validation_report(
    plan: Mapping[str, Any], validation_report: Any
) -> dict[str, Any]:
    """编排准入：报告必须绑定本计划的同一修订，且没有 error。"""
    if validation_report is None:
        raise OrchestrationError(
            VALIDATION_MISSING,
            "编排前必须先通过正式校验器；请提供同一修订的 ValidationReport",
            field_path="validation_report",
        )
    report = _plain(validation_report)
    if not isinstance(report, Mapping):
        raise OrchestrationError(
            VALIDATION_MISSING,
            "validation_report 必须是校验报告对象",
            field_path="validation_report",
        )
    target = report.get("target")
    target_kind = target.get("kind") if isinstance(target, Mapping) else None
    target_plan_id = target.get("plan_id") if isinstance(target, Mapping) else None
    target_revision = target.get("revision") if isinstance(target, Mapping) else None
    if (
        target_kind != "task_plan"
        or target_plan_id != plan["plan_id"]
        or target_revision != plan["revision"]
    ):
        raise OrchestrationError(
            VALIDATION_STALE,
            "校验报告与本计划不是同一修订（"
            f"报告为 {target_kind!r}/{target_plan_id!r}@{target_revision!r}，"
            f"计划为 {plan['plan_id']!r}@{plan['revision']!r}），请重新校验后再编排",
            field_path="validation_ref",
            details={
                "report_plan_id": target_plan_id,
                "report_revision": target_revision,
                "plan_id": plan["plan_id"],
                "revision": plan["revision"],
            },
        )
    if report.get("blocked") is True:
        summary_document = report.get("summary")
        errors = (
            summary_document.get("errors")
            if isinstance(summary_document, Mapping)
            else None
        )
        issues = [
            {
                "code": issue.get("code"),
                "severity": issue.get("severity"),
                "object_id": issue.get("object_id"),
                "field_path": issue.get("field_path"),
                "message": issue.get("message"),
            }
            for issue in (report.get("issues") or [])
            if isinstance(issue, Mapping)
        ]
        raise OrchestrationError(
            VALIDATION_BLOCKED,
            "校验报告 blocked=true：存在 error 级问题，禁止编排",
            field_path="validation_ref",
            details={"issues": issues, "errors": errors},
        )
    return dict(report)


def _check_sources(plan: Mapping[str, Any], spec: Any, template: Any) -> tuple[dict, dict]:
    if spec is None or template is None:
        raise OrchestrationError(
            PLAN_INVALID,
            "编排需要设计规格与工作流模板，用于计算 spec_digest / template_digest",
            field_path="spec_ref",
        )
    spec_document = _plain(spec)
    template_document = _plain(template)
    if not isinstance(spec_document, Mapping) or not isinstance(template_document, Mapping):
        raise OrchestrationError(
            PLAN_INVALID, "spec 与 template 必须是 JSON 对象", field_path="spec_ref"
        )
    spec_ref = plan.get("spec_ref")
    if isinstance(spec_ref, Mapping) and spec_ref:
        if spec_ref.get("spec_id") != spec_document.get("spec_id") or spec_ref.get(
            "version"
        ) != spec_document.get("version"):
            raise OrchestrationError(
                VALIDATION_STALE,
                "任务计划引用的规格版本与传入规格不一致，请基于当前规格重新生成并校验",
                field_path="spec_ref",
                details={
                    "plan_spec_ref": dict(spec_ref),
                    "spec": {
                        "spec_id": spec_document.get("spec_id"),
                        "version": spec_document.get("version"),
                    },
                },
            )
    template_ref = plan.get("template_ref")
    if isinstance(template_ref, Mapping) and template_ref:
        if template_ref.get("template_id") != template_document.get(
            "template_id"
        ) or template_ref.get("version") != template_document.get("version"):
            raise OrchestrationError(
                VALIDATION_STALE,
                "任务计划引用的模板版本与传入模板不一致，请基于当前模板重新规划",
                field_path="template_ref",
                details={
                    "plan_template_ref": dict(template_ref),
                    "template": {
                        "template_id": template_document.get("template_id"),
                        "version": template_document.get("version"),
                    },
                },
            )
    return dict(spec_document), dict(template_document)


def _execution_plan_id(plan_ref: Mapping[str, Any], config_digest: str, report_digest: str) -> str:
    """缺省执行计划编号：由内容摘要推导，保证同输入同编号（V07）。"""
    seed = f"{plan_ref['plan_id']}@{plan_ref['revision']}|{config_digest}|{report_digest}"
    return "EXEC-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _build_execution_plan(
    plan: Mapping[str, Any],
    graph: TaskGraph,
    schedule_result,
    *,
    spec_digest: str,
    template_digest: str,
    config_digest: str,
    report_digest: str,
    execution_plan_id: str,
) -> ExecutionPlan:
    return ExecutionPlan(
        schema_version=SCHEMA_VERSION,
        execution_plan_id=execution_plan_id,
        plan_ref=PlanRef(plan_id=plan["plan_id"], revision=plan["revision"]),
        spec_digest=spec_digest,
        template_digest=template_digest,
        config_digest=config_digest,
        nodes=[
            ExecutionNode(
                task_id=task_id,
                role=graph.roles[task_id],
                stage=graph.stages[task_id],
                priority=graph.priorities[task_id],
            )
            for task_id in graph.task_ids
        ],
        edges=[Edge(from_task=from_task, to_task=to_task) for from_task, to_task in graph.edges],
        topological_levels=[list(level) for level in graph.levels],
        batches=[list(batch) for batch in schedule_result.batches],
        bindings=[
            Binding(task_id=binding.task_id, global_slot=binding.global_slot, role_slot=binding.role_slot)
            for binding in schedule_result.bindings
        ],
        validation_ref=ValidationRef(
            plan_id=plan["plan_id"],
            revision=plan["revision"],
            report_digest=report_digest,
        ),
    )


@dataclass(frozen=True)
class OrchestrationResult:
    """一次编排的完整结果：契约对象 + 独立可导出的等待原因说明。"""

    execution_plan: ExecutionPlan
    explanation: dict[str, Any]

    def document(self) -> dict[str, Any]:
        """ExecutionPlan 的 JSON 结构（成员 5 存 artifacts / 成员 6 读图）。"""
        return to_jsonable(self.execution_plan)

    def explanation_document(self) -> dict[str, Any]:
        """等待原因说明的 JSON 结构；不属于 ExecutionPlan 契约，可单独保存。"""
        return self.explanation


def compile_plan(
    task_plan: Any,
    *,
    spec: Any,
    template: Any,
    validation_report: Any,
    execution_plan_id: str | None = None,
    verify: bool = False,
) -> OrchestrationResult:
    """把已校验的任务计划编译为执行计划。

    参数：
        task_plan：TaskPlan（契约 dataclass 或 JSON 对象），须带 ``plan_id``、
            ``revision``、``config`` 与非空 ``tasks``；
        spec / template：与计划引用版本一致的 DesignSpec / WorkflowTemplate，
            用于计算 ``spec_digest`` / ``template_digest``；
        validation_report：同一修订的 ValidationReport，``blocked`` 必须为 false；
        execution_plan_id：执行计划编号；缺省时由内容摘要推导，保证可复现；
        verify：为 True 时调用成员 3 的 ``validate_execution_plan`` 交叉核对结果。

    异常：见 :mod:`backend.app.orchestration.errors`，典型为
        ``ORCH_VALIDATION_MISSING`` / ``ORCH_VALIDATION_BLOCKED`` /
        ``ORCH_VALIDATION_STALE`` / ``ORCH_CONFIG_INVALID`` / ``ORCH_BLOCKED``。
    """
    plan = _plan_document(task_plan)
    report = _check_validation_report(plan, validation_report)
    spec_document, template_document = _check_sources(plan, spec, template)

    config = RunConfig.parse(plan.get("config"))
    graph = build_graph(plan, ordering=config.ordering)
    schedule_result = schedule(graph, config)

    spec_digest = content_digest(spec_document)
    template_digest = content_digest(template_document)
    config_digest = content_digest(plan["config"])
    report_digest = content_digest(report)
    plan_ref = {"plan_id": plan["plan_id"], "revision": plan["revision"]}
    plan_id_value = execution_plan_id or _execution_plan_id(
        plan_ref, config_digest, report_digest
    )

    execution_plan = _build_execution_plan(
        plan,
        graph,
        schedule_result,
        spec_digest=spec_digest,
        template_digest=template_digest,
        config_digest=config_digest,
        report_digest=report_digest,
        execution_plan_id=plan_id_value,
    )
    explanation = schedule_result.explanation(
        graph,
        plan_ref=plan_ref,
        execution_plan_id=plan_id_value,
        schema_version=SCHEMA_VERSION,
    )
    result = OrchestrationResult(execution_plan=execution_plan, explanation=explanation)

    if verify:
        _self_check(result, plan, report, spec_document, template_document)
    return result


def _self_check(
    result: OrchestrationResult,
    plan: Mapping[str, Any],
    report: Mapping[str, Any],
    spec: Mapping[str, Any],
    template: Mapping[str, Any],
) -> None:
    """用成员 3 的执行计划校验器复核本模块输出（不应出现任何问题）。"""
    from ..validation import validate_execution_plan

    checked = validate_execution_plan(
        result.document(),
        task_plan=plan,
        validation_report=report,
        spec=spec,
        template=template,
    )
    if checked.blocked:
        raise OrchestrationError(
            SELF_CHECK_FAILED,
            "编排结果未通过成员 3 的执行计划校验，说明与契约不一致",
            field_path="execution_plan",
            details={"issues": to_jsonable(checked.issues)},
            status=500,
        )


def summary(result: OrchestrationResult) -> str:
    """一行摘要，供 CLI、作业详情与界面状态使用。"""
    plan = result.execution_plan
    waits = result.explanation.get("summary", {}).get("wait_reasons", 0)
    return (
        f"{len(plan.nodes)} 节点 / {len(plan.edges)} 边 / "
        f"{len(plan.topological_levels)} 层 / {len(plan.batches)} 批 / 等待 {waits} 条"
    )


def explain_blocker(error: OrchestrationError) -> str:
    """把阻塞错误转成一句可展示的说明（作业状态 / 界面提示用）。"""
    if error.code != BLOCKED:
        return error.message
    unscheduled = error.details.get("unscheduled") or []
    return f"{error.message}；未安排任务：{'、'.join(unscheduled)}"


__all__ = [
    "OrchestrationResult",
    "compile_plan",
    "explain_blocker",
    "summary",
]
