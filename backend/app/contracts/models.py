"""共享数据契约的单一来源。

本模块用 dataclass 定义成员 3 交付的全部共享对象：
DesignSpec、WorkflowTemplate、TaskPlan、ExecutionPlan、ValidationReport。
字段类型、枚举与最小约束只在这里维护，JSON Schema
（backend/app/contracts/schema/*.schema.json）由 schema.py 从此处导出，
结构校验也由同一份 Schema 驱动，避免多处手写造成漂移。

命名与版本约定：
- ``schema_version`` 表示文档格式版本，当前为 ``"1.0"``；
- ``version`` / ``revision`` 表示业务修订，从 1 开始递增；
- 内容摘要（digest）由 :func:`content_digest` 计算：键固定排序、
  数组保留契约要求的顺序，再做 SHA-256，输出 64 位小写十六进制。

注意：``dict[str, int]`` 在本契约里约定为“正整数映射”（角色容量），
生成 Schema 时其值为 ``minimum: 1`` 的整数。
"""

from __future__ import annotations

import hashlib
import json
import types
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from typing import Any, Optional, get_args, get_origin, get_type_hints

SCHEMA_VERSION = "1.0"
ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
CODE_PATTERN = r"^[A-Z][A-Z0-9_]*$"

TASK_KINDS = ("implementation", "verification", "integration", "support")
ARTIFACT_TYPES = (
    "design_contract",
    "implementation",
    "verification_report",
    "integration_report",
    "support_artifact",
)
INPUT_SOURCES = ("design", "task_output")
GENERATION_MODES = ("live_llm", "offline_rule", "fixture_replay", "curated_example")
SEVERITIES = ("error", "warning")
CONSTRAINT_STATUSES = ("specified", "not_applicable", "unspecified")
ORDERING_KEYS = ("priority", "task_id")
COVERAGE_STATUSES = (
    "covered",
    "missing_implementation",
    "missing_verification",
    "missing_both",
)
REPORT_TARGET_KINDS = ("design_spec", "task_plan", "execution_plan")


class ContractError(ValueError):
    """契约数据无法按模型构造或序列化时抛出。"""


def fld(
    *,
    default: Any = MISSING,
    default_factory: Any = MISSING,
    pattern: Optional[str] = None,
    choices: Optional[tuple] = None,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
    exclusive_minimum: Optional[int] = None,
    min_items: Optional[int] = None,
    min_length: Optional[int] = None,
    min_properties: Optional[int] = None,
    items_pattern: Optional[str] = None,
    items_choices: Optional[tuple] = None,
    allow_empty: bool = False,
    description: Optional[str] = None,
):
    """声明字段并携带生成 JSON Schema 所需的约束元数据。"""
    meta: dict[str, Any] = {}
    if pattern is not None:
        meta["pattern"] = pattern
    if choices is not None:
        meta["choices"] = tuple(choices)
    if minimum is not None:
        meta["minimum"] = minimum
    if maximum is not None:
        meta["maximum"] = maximum
    if exclusive_minimum is not None:
        meta["exclusive_minimum"] = exclusive_minimum
    if min_items is not None:
        meta["min_items"] = min_items
    if min_length is not None:
        meta["min_length"] = min_length
    if min_properties is not None:
        meta["min_properties"] = min_properties
    if items_pattern is not None:
        meta["items_pattern"] = items_pattern
    if items_choices is not None:
        meta["items_choices"] = tuple(items_choices)
    if allow_empty:
        meta["allow_empty"] = True
    if description is not None:
        meta["description"] = description
    kwargs: dict[str, Any] = {}
    if default is not MISSING:
        kwargs["default"] = default
    elif default_factory is not MISSING:
        kwargs["default_factory"] = default_factory
    return field(metadata=meta, **kwargs)


# ---------------------------------------------------------------------------
# 1. 规格 DesignSpec
# ---------------------------------------------------------------------------


@dataclass
class Requirement:
    """一条稳定编号的需求。"""

    id: str = fld(pattern=ID_PATTERN, description="需求稳定编号，如 R1")
    goal: str = fld(description="需求希望实现的行为")
    acceptance: str = fld(description="可验证的验收口径")


@dataclass
class Scope:
    """产品范围：目标、首期功能、明确排除项。"""

    goal: str = fld(description="产品目标")
    included: list[str] = fld(min_items=1, description="首期包含的能力")
    excluded: list[str] = fld(description="一期明确排除项，可为空")


@dataclass
class Module:
    """模块职责及其关联需求。"""

    id: str = fld(pattern=ID_PATTERN)
    responsibility: str = fld()
    requirement_ids: list[str] = fld(items_pattern=ID_PATTERN, description="关联需求编号")


@dataclass
class DesignItem:
    """设计内容条目：页面、接口、数据实体或一致性规则。"""

    id: str = fld(pattern=ID_PATTERN)
    kind: str = fld(description="page / interface / entity / rule / other")
    description: str = fld()
    requirement_ids: list[str] = fld(items_pattern=ID_PATTERN)


@dataclass
class Constraint:
    """约束条件；未指定与不适用必须分别记录。"""

    id: str = fld(pattern=ID_PATTERN)
    category: str = fld(description="stack / platform / security / infrastructure / other")
    detail: str = fld()
    status: str = fld(choices=CONSTRAINT_STATUSES)


@dataclass
class DesignSpec:
    """成员 1 输入、成员 2/3 共同消费的设计规格。"""

    schema_version: str = fld(choices=(SCHEMA_VERSION,))
    spec_id: str = fld(pattern=ID_PATTERN)
    version: int = fld(minimum=1)
    product: str = fld()
    scope: Scope = fld()
    requirements: list[Requirement] = fld(min_items=1)
    modules: list[Module] = fld(min_items=1)
    design_items: list[DesignItem] = fld(min_items=1)
    constraints: list[Constraint] = fld(min_items=1)


# ---------------------------------------------------------------------------
# 2. 工作流模板 WorkflowTemplate
# ---------------------------------------------------------------------------


@dataclass
class WorkflowTemplate:
    """编排配置：阶段、角色、容量与排序依据。"""

    schema_version: str = fld(choices=(SCHEMA_VERSION,))
    template_id: str = fld(pattern=ID_PATTERN)
    version: int = fld(minimum=1)
    name: str = fld()
    description: str = fld()
    stages: list[str] = fld(min_items=1, description="允许的任务阶段")
    allowed_roles: list[str] = fld(min_items=1, description="允许的角色")
    require_verification: bool = fld(description="是否要求每条需求都有验证任务")


# ---------------------------------------------------------------------------
# 3. 任务计划 TaskPlan
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    """本次编排的运行配置。"""

    max_concurrency: int = fld(minimum=1)
    role_capacity: dict[str, int] = fld(min_properties=1, description="角色 -> 容量，正整数")
    ordering: list[str] = fld(
        min_items=1,
        items_choices=ORDERING_KEYS,
        description="排序键，按顺序生效",
    )


@dataclass
class SpecRef:
    spec_id: str = fld(pattern=ID_PATTERN)
    version: int = fld(minimum=1)


@dataclass
class TemplateRef:
    template_id: str = fld(pattern=ID_PATTERN)
    version: int = fld(minimum=1)


@dataclass
class PlanRef:
    plan_id: str = fld(pattern=ID_PATTERN)
    revision: int = fld(minimum=1)


@dataclass
class InputRef:
    """任务输入：要么引用设计条目，要么引用其他任务的产物。"""

    source: str = fld(choices=INPUT_SOURCES)
    ref: str = fld(min_length=1, description="设计条目 ID 或 <task_id>/<output_id>")
    type: str = fld(choices=ARTIFACT_TYPES)


@dataclass
class OutputRef:
    id: str = fld(pattern=ID_PATTERN, description="产物 ID，在计划内唯一")
    type: str = fld(choices=ARTIFACT_TYPES)
    description: str = fld()


@dataclass
class Dependency:
    """依赖边：task_id 是当前任务的前置任务。"""

    task_id: str = fld(pattern=ID_PATTERN)
    reason: str = fld(description="为什么需要该前置任务")


@dataclass
class Estimate:
    value: int = fld(minimum=1, description="估算数值（正整数）")
    unit: str = fld(description="单位，如 人时")
    basis: str = fld(description="估算依据")


@dataclass
class Clarification:
    id: str = fld(pattern=ID_PATTERN)
    question: str = fld()
    related_ids: list[str] = fld(description="关联的需求/模块/设计条目")


@dataclass
class Task:
    """任务卡，任务计划中的最小调度单元。"""

    id: str = fld(pattern=ID_PATTERN)
    title: str = fld()
    description: str = fld()
    requirement_ids: list[str] = fld(min_items=1, items_pattern=ID_PATTERN)
    module_ids: list[str] = fld(items_pattern=ID_PATTERN)
    design_refs: list[str] = fld(items_pattern=ID_PATTERN)
    kind: str = fld(choices=TASK_KINDS)
    stage: str = fld()
    role: str = fld()
    inputs: list[InputRef] = fld()
    outputs: list[OutputRef] = fld(min_items=1)
    acceptance: list[str] = fld(min_items=1)
    depends_on: list[Dependency] = fld()
    priority: int = fld(minimum=0, description="非负整数，越小越优先")
    estimated_effort: Estimate = fld()


@dataclass
class TaskPlan:
    """计划草稿：绑定一个规格修订与模板修订，可反复校验。"""

    schema_version: str = fld(choices=(SCHEMA_VERSION,))
    plan_id: str = fld(pattern=ID_PATTERN)
    revision: int = fld(minimum=1)
    spec_ref: SpecRef = fld()
    template_ref: TemplateRef = fld()
    config: RunConfig = fld()
    generation_mode: str = fld(choices=GENERATION_MODES)
    tasks: list[Task] = fld(min_items=1)
    assumptions: list[str] = fld(default_factory=list)
    clarifications: list[Clarification] = fld(default_factory=list)


# ---------------------------------------------------------------------------
# 4. 执行计划 ExecutionPlan
# ---------------------------------------------------------------------------


@dataclass
class ExecutionNode:
    task_id: str = fld(pattern=ID_PATTERN)
    role: str = fld()
    stage: str = fld()
    priority: int = fld(minimum=0)


@dataclass
class Edge:
    """有向边：from_task 为前置任务，to_task 为依赖它的当前任务。"""

    from_task: str = fld(pattern=ID_PATTERN)
    to_task: str = fld(pattern=ID_PATTERN)


@dataclass
class Binding:
    task_id: str = fld(pattern=ID_PATTERN)
    global_slot: int = fld(minimum=1)
    role_slot: int = fld(minimum=1)


@dataclass
class ValidationRef:
    plan_id: str = fld(pattern=ID_PATTERN)
    revision: int = fld(minimum=1)
    report_digest: str = fld(pattern=DIGEST_PATTERN)


@dataclass
class ExecutionPlan:
    """编译产物：每个任务只安排一次，边保证生产者在前。"""

    schema_version: str = fld(choices=(SCHEMA_VERSION,))
    execution_plan_id: str = fld(pattern=ID_PATTERN)
    plan_ref: PlanRef = fld()
    spec_digest: str = fld(pattern=DIGEST_PATTERN)
    template_digest: str = fld(pattern=DIGEST_PATTERN)
    config_digest: str = fld(pattern=DIGEST_PATTERN)
    nodes: list[ExecutionNode] = fld(min_items=1)
    edges: list[Edge] = fld()
    topological_levels: list[list[str]] = fld(min_items=1)
    batches: list[list[str]] = fld(min_items=1)
    bindings: list[Binding] = fld(min_items=1)
    validation_ref: ValidationRef = fld()


# ---------------------------------------------------------------------------
# 5. 校验报告 ValidationReport
# ---------------------------------------------------------------------------


@dataclass
class ReportTarget:
    """报告绑定的对象；按 kind 使用不同的引用字段。"""

    kind: str = fld(choices=REPORT_TARGET_KINDS)
    spec_id: Optional[str] = fld(default=None, pattern=ID_PATTERN)
    version: Optional[int] = fld(default=None, minimum=1)
    plan_id: Optional[str] = fld(default=None, pattern=ID_PATTERN)
    revision: Optional[int] = fld(default=None, minimum=1)
    execution_plan_id: Optional[str] = fld(default=None, pattern=ID_PATTERN)


@dataclass
class ValidationIssue:
    """统一问题对象，所有检查面共用。"""

    code: str = fld(pattern=CODE_PATTERN)
    severity: str = fld(choices=SEVERITIES)
    object_id: str = fld(description="问题所在对象 ID")
    field_path: str = fld(description="JSON 路径，如 tasks[0].depends_on[1].task_id")
    message: str = fld()
    related_ids: list[str] = fld(default_factory=list)
    suggested_action: str = fld(default="", allow_empty=True)


@dataclass
class Summary:
    errors: int = fld(minimum=0)
    warnings: int = fld(minimum=0)


@dataclass
class RequirementCoverage:
    requirement_id: str = fld(pattern=ID_PATTERN)
    implementation_task_ids: list[str] = fld(items_pattern=ID_PATTERN)
    verification_task_ids: list[str] = fld(items_pattern=ID_PATTERN)
    status: str = fld(choices=COVERAGE_STATUSES)


@dataclass
class Coverage:
    requirements: list[RequirementCoverage] = fld()
    implementation_ratio: float = fld(minimum=0, maximum=1)
    verification_ratio: float = fld(minimum=0, maximum=1)


@dataclass
class ValidationReport:
    """一次校验的完整结果；error 阻止编排/确认，warning 在确认时记录。"""

    schema_version: str = fld(choices=(SCHEMA_VERSION,))
    target: ReportTarget = fld()
    content_digest: str = fld(
        pattern=DIGEST_PATTERN, description="被校验对象（规格或计划）的内容摘要"
    )
    blocked: bool = fld(description="是否存在 error 级问题")
    summary: Summary = fld()
    issues: list[ValidationIssue] = fld()
    coverage: Optional[Coverage] = fld(default=None)


# ---------------------------------------------------------------------------
# 序列化 / 反序列化 / 内容摘要
# ---------------------------------------------------------------------------


def to_jsonable(value: Any) -> Any:
    """把 dataclass 文档递归转换为可直接 json.dumps 的结构。

    可选字段为 ``None`` 时直接省略，而不是写成 ``null``：契约 Schema 用“字段缺省”
    表示可选，``null`` 不是合法值，报告才能同时通过结构校验与摘要计算。
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in fields(value)
            if getattr(value, f.name) is not None
        }
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    return value


def canonical_json(value: Any, *, sort_keys: bool = True) -> str:
    """规范化 JSON 文本：UTF-8 原文、紧凑分隔符、键排序。"""
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_digest(value: Any) -> str:
    """计算契约内容摘要（SHA-256，64 位小写十六进制）。"""
    payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_optional(annotation: Any) -> tuple[bool, Any]:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return True, args[0] if len(args) == 1 else annotation
    return False, annotation


def _coerce(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    optional, inner = _is_optional(annotation)
    if optional:
        return _coerce(inner, value)
    origin = get_origin(annotation)
    if origin is list:
        (item_type,) = get_args(annotation)
        return [_coerce(item_type, item) for item in value]
    if origin is dict:
        _, value_type = get_args(annotation)
        return {str(k): _coerce(value_type, v) for k, v in value.items()}
    if is_dataclass(annotation) and not isinstance(annotation, type):
        raise ContractError("模型注解必须是类型，而不是实例")
    if is_dataclass(annotation):
        return from_dict(annotation, value)
    if annotation is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def from_dict(cls: type, data: Any) -> Any:
    """按 dataclass 模型构造对象；缺失必填字段时抛出 :class:`ContractError`。"""
    if not is_dataclass(cls):
        raise ContractError(f"{cls!r} 不是契约模型")
    if not isinstance(data, dict):
        raise ContractError(f"{cls.__name__} 需要 JSON 对象，实际为 {type(data).__name__}")
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            if f.default is not MISSING or f.default_factory is not MISSING:
                continue
            raise ContractError(f"{cls.__name__} 缺少必填字段 {f.name}")
        kwargs[f.name] = _coerce(hints[f.name], data[f.name])
    return cls(**kwargs)


def build_report(
    *,
    target: ReportTarget,
    target_document: Any,
    issues: list[ValidationIssue],
    coverage: Optional[Coverage] = None,
) -> ValidationReport:
    """组装校验报告并计算摘要与计数。"""
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    return ValidationReport(
        schema_version=SCHEMA_VERSION,
        target=target,
        content_digest=content_digest(target_document),
        blocked=errors > 0,
        summary=Summary(errors=errors, warnings=warnings),
        issues=issues,
        coverage=coverage,
    )


__all__ = [
    "SCHEMA_VERSION",
    "ID_PATTERN",
    "DIGEST_PATTERN",
    "CODE_PATTERN",
    "TASK_KINDS",
    "ARTIFACT_TYPES",
    "INPUT_SOURCES",
    "GENERATION_MODES",
    "SEVERITIES",
    "CONSTRAINT_STATUSES",
    "ORDERING_KEYS",
    "COVERAGE_STATUSES",
    "REPORT_TARGET_KINDS",
    "ContractError",
    "Requirement",
    "Scope",
    "Module",
    "DesignItem",
    "Constraint",
    "DesignSpec",
    "WorkflowTemplate",
    "RunConfig",
    "SpecRef",
    "TemplateRef",
    "PlanRef",
    "InputRef",
    "OutputRef",
    "Dependency",
    "Estimate",
    "Clarification",
    "Task",
    "TaskPlan",
    "ExecutionNode",
    "Edge",
    "Binding",
    "ValidationRef",
    "ExecutionPlan",
    "ReportTarget",
    "ValidationIssue",
    "Summary",
    "RequirementCoverage",
    "Coverage",
    "ValidationReport",
    "to_jsonable",
    "canonical_json",
    "content_digest",
    "from_dict",
    "build_report",
]
