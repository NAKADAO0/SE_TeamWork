"""旧案例迁移工具：把 ``examples/*.json`` 的简化格式规范化为契约草稿。

设计文档要求"旧案例中的多需求来源规范化为 requirement_ids 数组"，同时旧案例
缺少模块、设计条目、产物连接、估算等字段。本模块不修改任何原始文件，只产出

* 一个符合契约结构（可被 ``validate_design_spec`` / ``validate_task_plan`` 接收）
  的草稿字典；
* 一份缺口报告（``MigrationGap`` 列表），逐条说明哪些字段是推断或占位、需要
  人工复核。迁移脚本/人工补全后，缺口应清零。

原始 ``examples/chat.json``、``examples/chat-plan.json`` 等保持原样，避免破坏
既有 demo；迁移结果写入 ``examples/contracts/`` 下的新目录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import (
    GENERATION_MODES,
    SCHEMA_VERSION,
    TaskPlan,
    to_jsonable,
)
from ..validation import codes as C

# 旧案例的 ``source`` 字段可能用顿号、逗号或分号分隔多个需求 ID。
_SOURCE_SPLIT = re.compile(r"[、,，;；/]+")

# 旧案例出现过的角色到模板角色的固定映射；模板之外的角色原样保留。
_ROLE_MAP = {"developer": "backend"}

_KIND_STAGE = {
    "implementation": "implement",
    "verification": "verify",
    "integration": "integrate",
    "support": "implement",
}

_OUTPUT_TYPE = {
    "implementation": "implementation",
    "verification": "verification_report",
    "integration": "integration_report",
    "support": "support_artifact",
}


@dataclass
class MigrationGap:
    """迁移缺口：说明某字段由旧案例推断/占位，需要人工复核。"""

    code: str
    object_id: str
    field_path: str
    message: str
    related_ids: list[str] = field(default_factory=list)
    suggested_action: str = ""

    @property
    def severity(self) -> str:
        return "warning"

    def to_dict(self) -> dict[str, Any]:
        data = to_jsonable(self)
        data["severity"] = self.severity
        return data


@dataclass
class MigrationResult:
    """一次迁移的产出：契约草稿 + 缺口报告。"""

    document: dict[str, Any]
    gaps: list[MigrationGap] = field(default_factory=list)

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "gap_count": self.gap_count,
            "gaps": [gap.to_dict() for gap in self.gaps],
        }


def _source_ids(source: Any) -> list[str]:
    if not isinstance(source, str):
        return []
    return [part.strip() for part in _SOURCE_SPLIT.split(source) if part.strip()]


def _infer_kind(legacy_task: dict[str, Any]) -> str:
    role = str(legacy_task.get("role", ""))
    title = str(legacy_task.get("title", ""))
    if role == "qa" and any(word in title for word in ("联调", "验收", "端到端")):
        return "integration"
    if role == "qa" or "验证" in title:
        return "verification"
    return "implementation"


def _normalize_requirement(requirement: Any) -> dict[str, Any]:
    if not isinstance(requirement, dict):
        requirement = {}
    return {
        "id": str(requirement.get("id", "")),
        "goal": str(requirement.get("goal", "")),
        "acceptance": str(requirement.get("acceptance", "")),
    }


def normalize_legacy_spec(
    legacy: dict[str, Any],
    *,
    spec_id: str = "SPEC-LEGACY",
    version: int = 1,
) -> MigrationResult:
    """把旧版简化规格规范化为 ``DesignSpec`` 草稿。"""
    product = str(legacy.get("product", ""))
    raw_requirements = legacy.get("requirements")
    requirements = [
        _normalize_requirement(item)
        for item in (raw_requirements if isinstance(raw_requirements, list) else [])
    ]
    raw_scope = legacy.get("scope")
    if isinstance(raw_scope, dict):
        scope = {
            "goal": str(raw_scope.get("goal") or product),
            "included": [str(item) for item in raw_scope.get("included") or []]
            or [requirement["goal"] for requirement in requirements],
            "excluded": [str(item) for item in raw_scope.get("excluded") or []],
        }
    else:
        scope = {
            "goal": str(raw_scope or product),
            "included": [requirement["goal"] for requirement in requirements],
            "excluded": [],
        }

    document = {
        "schema_version": SCHEMA_VERSION,
        "spec_id": spec_id,
        "version": version,
        "product": product,
        "scope": scope,
        "requirements": requirements,
        "modules": [],
        "design_items": [],
        "constraints": [
            {
                "id": "C-LEGACY",
                "category": "other",
                "detail": "旧案例未记录约束条件，迁移后需补充技术栈/平台/安全/基础设施约束",
                "status": "unspecified",
            }
        ],
    }

    gaps: list[MigrationGap] = [
        MigrationGap(
            code=C.LEGACY_SCOPE_INFERRED,
            object_id=spec_id,
            field_path="scope",
            message="旧案例仅有 scope 文本（或没有），已推断为 goal/included/excluded 结构",
            suggested_action="确认首期功能 included 与明确排除项 excluded 是否符合产品意图",
        ),
        MigrationGap(
            code=C.LEGACY_MODULES_MISSING,
            object_id=spec_id,
            field_path="modules",
            message="旧案例没有模块职责，设计规格缺少 module_ids 的引用来源",
            suggested_action="按设计文档 §3.1 补充模块 ID、职责与关联需求",
        ),
        MigrationGap(
            code=C.LEGACY_DESIGN_ITEMS_MISSING,
            object_id=spec_id,
            field_path="design_items",
            message="旧案例没有页面/接口/实体/规则等设计条目",
            suggested_action="补充 design_items，供任务卡的 design_refs 引用",
        ),
        MigrationGap(
            code=C.LEGACY_CONSTRAINTS_MISSING,
            object_id=spec_id,
            field_path="constraints",
            message="旧案例没有约束条件，已插入 status=unspecified 的占位约束",
            suggested_action="逐类记录技术栈、平台、安全、基础设施约束；不适用也要显式记录",
        ),
    ]
    return MigrationResult(document=document, gaps=gaps)


def normalize_legacy_plan(
    legacy: dict[str, Any],
    *,
    spec_id: str = "SPEC-LEGACY",
    spec_version: int = 1,
    template_id: str = "TPL-DEFAULT",
    template_version: int = 1,
    plan_id: str = "PLAN-LEGACY",
    revision: int = 1,
) -> MigrationResult:
    """把旧版完整计划规范化为 ``TaskPlan`` 草稿。"""
    gaps: list[MigrationGap] = []
    raw_tasks = legacy.get("tasks")
    raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []

    tasks: list[dict[str, Any]] = []
    roles: set[str] = set()
    for index, legacy_task in enumerate(raw_tasks):
        if not isinstance(legacy_task, dict):
            legacy_task = {}
        task_id = str(legacy_task.get("id", ""))
        title = str(legacy_task.get("title", ""))
        kind = _infer_kind(legacy_task)
        mapped_role = _ROLE_MAP.get(str(legacy_task.get("role", "")), str(legacy_task.get("role", "")))
        roles.add(mapped_role)

        requirement_ids = _source_ids(legacy_task.get("source"))
        if not requirement_ids:
            gaps.append(
                MigrationGap(
                    code=C.LEGACY_SOURCE_MISSING,
                    object_id=task_id,
                    field_path=f"tasks[{index}].requirement_ids",
                    message="旧案例任务缺少 source（需求来源），无法建立需求追溯",
                    suggested_action="补充该任务对应的需求 ID",
                )
            )

        depends_on: list[dict[str, str]] = []
        raw_depends = legacy_task.get("depends_on")
        for dependency in raw_depends if isinstance(raw_depends, list) else []:
            dependency_id = str(dependency.get("task_id") if isinstance(dependency, dict) else dependency)
            if not dependency_id:
                continue
            depends_on.append(
                {
                    "task_id": dependency_id,
                    "reason": "沿用旧案例依赖关系，具体理由待补充",
                }
            )

        acceptance = legacy_task.get("acceptance")
        acceptance_list = [str(acceptance)] if isinstance(acceptance, str) and acceptance else []
        if not acceptance_list:
            acceptance_list = ["（旧案例未记录验收标准，需补充）"]
            gaps.append(
                MigrationGap(
                    code=C.LEGACY_ACCEPTANCE_MISSING,
                    object_id=task_id,
                    field_path=f"tasks[{index}].acceptance",
                    message="旧案例任务缺少验收标准，已写入占位文案",
                    suggested_action="按需求条目补全可检查的验收标准",
                )
            )

        raw_output = legacy_task.get("output")
        if isinstance(raw_output, dict):
            output_id = str(raw_output.get("id") or f"O-{task_id}")
            output_description = str(raw_output.get("description") or "")
            output_type = str(raw_output.get("type") or _OUTPUT_TYPE[kind])
        else:
            output_id = f"O-{task_id}"
            output_description = str(raw_output or "（旧案例未记录产物描述，需补充）")
            output_type = _OUTPUT_TYPE[kind]
            gaps.append(
                MigrationGap(
                    code=C.LEGACY_OUTPUT_INFERRED,
                    object_id=task_id,
                    field_path=f"tasks[{index}].outputs[0]",
                    message=f"旧案例仅记录输出描述文本，已推断产物 ID={output_id}、type={output_type}",
                    suggested_action="确认产物 ID 与类型，并补充其消费者任务",
                )
            )

        task = {
            "id": task_id,
            "title": title,
            "description": f"{title}（由旧案例 examples/*.json 迁移，描述待补充）",
            "requirement_ids": requirement_ids,
            "module_ids": [],
            "design_refs": [],
            "kind": kind,
            "stage": _KIND_STAGE[kind],
            "role": mapped_role,
            "inputs": [],
            "outputs": [
                {
                    "id": output_id,
                    "type": output_type,
                    "description": output_description,
                }
            ],
            "acceptance": acceptance_list,
            "depends_on": depends_on,
            "priority": int(legacy_task.get("priority", 1)),
            "estimated_effort": {
                "value": 1,
                "unit": "人时",
                "basis": "旧案例未记录估算，迁移占位值需按实际复核",
            },
        }
        tasks.append(task)

        gaps.append(
            MigrationGap(
                code=C.LEGACY_KIND_INFERRED,
                object_id=task_id,
                field_path=f"tasks[{index}].kind",
                message=f"旧案例没有任务类型，按 role/title 推断为 {kind}",
                suggested_action="确认任务类型（实现/验证/集成/支撑）",
            )
        )
        gaps.append(
            MigrationGap(
                code=C.LEGACY_MODULE_IDS_MISSING,
                object_id=task_id,
                field_path=f"tasks[{index}].module_ids",
                message="旧案例没有模块归属，module_ids 迁移为空数组",
                suggested_action="按模块职责补充 module_ids",
            )
        )
        gaps.append(
            MigrationGap(
                code=C.LEGACY_DESIGN_REFS_MISSING,
                object_id=task_id,
                field_path=f"tasks[{index}].design_refs",
                message="旧案例没有设计条目引用，design_refs 迁移为空数组",
                suggested_action="补充 design_refs；校验器会对非 support 任务给出告警",
            )
        )
        gaps.append(
            MigrationGap(
                code=C.LEGACY_INPUTS_MISSING,
                object_id=task_id,
                field_path=f"tasks[{index}].inputs",
                message="旧案例没有输入/产物连接声明，inputs 迁移为空数组",
                suggested_action="按设计文档 §4.3 声明 design/task_output 输入并补直接依赖",
            )
        )
        if depends_on:
            gaps.append(
                MigrationGap(
                    code=C.LEGACY_DEPENDENCY_REASON_PLACEHOLDER,
                    object_id=task_id,
                    field_path=f"tasks[{index}].depends_on",
                    message="旧案例只记录前置任务 ID，依赖理由使用占位文案",
                    suggested_action="为每条依赖补充可审阅的 reason",
                )
            )
        gaps.append(
            MigrationGap(
                code=C.LEGACY_PRIORITY_DEFAULT,
                object_id=task_id,
                field_path=f"tasks[{index}].priority",
                message="旧案例没有优先级，统一迁移为 priority=1",
                suggested_action="按业务优先级调整（非负整数，越小越优先）",
            )
        )
        gaps.append(
            MigrationGap(
                code=C.LEGACY_ESTIMATE_PLACEHOLDER,
                object_id=task_id,
                field_path=f"tasks[{index}].estimated_effort",
                message="旧案例没有工作量估算，迁移为占位的 1 人时",
                suggested_action="按团队口径补充估算值与依据",
            )
        )

    capacity = legacy.get("capacity", 1)
    try:
        max_concurrency = max(1, int(capacity))
    except (TypeError, ValueError):
        max_concurrency = 1
    role_capacity = {role: 1 for role in sorted(roles) if role}
    legacy_mode = legacy.get("mode")
    generation_mode = legacy_mode if legacy_mode in GENERATION_MODES else "curated_example"

    document = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "revision": revision,
        "spec_ref": {"spec_id": spec_id, "version": spec_version},
        "template_ref": {"template_id": template_id, "version": template_version},
        "config": {
            "max_concurrency": max_concurrency,
            "role_capacity": role_capacity,
            "ordering": ["priority", "task_id"],
        },
        "generation_mode": generation_mode,
        "tasks": tasks,
        "assumptions": [
            "由旧案例 examples/*.json 迁移生成；缺口见迁移报告，补全前不要直接确认",
        ],
        "clarifications": [],
    }

    gaps.extend(
        [
            MigrationGap(
                code=C.LEGACY_CONFIG_INFERRED,
                object_id=plan_id,
                field_path="config",
                message="旧案例只记录全局 capacity，已迁移为 max_concurrency 与 ordering",
                suggested_action="确认模板版本、排序键与并发配置",
            ),
            MigrationGap(
                code=C.LEGACY_CAPACITY_MAPPED,
                object_id=plan_id,
                field_path="config.role_capacity",
                message=f"角色容量按任务实际出现的角色统一迁移为 1（{sorted(roles)}）",
                suggested_action="按真实人员/资源容量调整 role_capacity",
            ),
        ]
    )
    return MigrationResult(document=document, gaps=gaps)


def normalize_legacy_examples(
    legacy_spec: dict[str, Any],
    legacy_plan: dict[str, Any],
    *,
    spec_id: str = "SPEC-LEGACY",
    plan_id: str = "PLAN-LEGACY",
    template_id: str = "TPL-DEFAULT",
) -> tuple[MigrationResult, MigrationResult]:
    """同时迁移一对旧版规格 + 计划，返回 (规格结果, 计划结果)。"""
    spec_result = normalize_legacy_spec(legacy_spec, spec_id=spec_id)
    plan_result = normalize_legacy_plan(
        legacy_plan,
        spec_id=spec_id,
        spec_version=spec_result.document["version"],
        template_id=template_id,
        plan_id=plan_id,
    )
    return spec_result, plan_result


def normalized_task_plan(document: dict[str, Any]) -> TaskPlan:
    """把迁移草稿构造成强类型 ``TaskPlan``；字段不合法时抛 ``ContractError``。"""
    from .models import from_dict

    return from_dict(TaskPlan, document)


__all__ = [
    "MigrationGap",
    "MigrationResult",
    "normalize_legacy_spec",
    "normalize_legacy_plan",
    "normalize_legacy_examples",
    "normalized_task_plan",
]
