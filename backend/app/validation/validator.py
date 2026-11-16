"""契约校验器：结构检查 + 7 个检查面。

对应设计文档 §5.1：
1. 数据结构：由模型导出的 JSON Schema 驱动（structure.check_structure）；
2. 来源引用：需求/模块/设计条目存在且版本一致；
3. 需求覆盖：每条需求都有实现任务与验证任务；
4. 图结构：前置存在、无自依赖、无环并给出真实闭合路径；
5. 产物连接：输入引用的产物存在、类型相符、消费者声明了直接依赖；
6. 角色与容量：角色可用、容量与全局并发为正整数；
7. 修订与约束：引用当前修订、拒绝基于陈旧修订的报告。

所有问题统一输出 ValidationIssue；error 阻止编排/确认，warning 在确认时记录。
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts.models import (
    Coverage,
    ReportTarget,
    RequirementCoverage,
    ValidationIssue,
    ValidationReport,
    build_report,
    content_digest,
    to_jsonable,
)
from ..contracts.schema import contract_schemas
from . import codes as C
from .structure import check_structure

Document = dict[str, Any]

_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


def _schema(key: str) -> dict[str, Any]:
    if not _SCHEMA_CACHE:
        _SCHEMA_CACHE.update(contract_schemas())
    return _SCHEMA_CACHE[key]


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    return to_jsonable(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _issue(
    code: str,
    *,
    object_id: str,
    field_path: str,
    message: str,
    related_ids: list[str] | None = None,
    suggested_action: str = "",
    severity: str = "error",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        object_id=object_id or "document",
        field_path=field_path or "$",
        message=message,
        related_ids=list(related_ids or []),
        suggested_action=suggested_action,
    )


def _enrich_structure_ids(issues: list[ValidationIssue], document: Any) -> None:
    """把结构问题的 object_id 从文档级细化到任务/节点级。"""
    doc = _as_dict(document)
    tasks = _as_dict_list(doc.get("tasks"))
    nodes = _as_dict_list(doc.get("nodes"))
    for issue in issues:
        path = issue.field_path or ""
        match = re.match(r"tasks\[(\d+)\]", path)
        if match:
            index = int(match.group(1))
            if index < len(tasks):
                task_id = _str(tasks[index].get("id"))
                if task_id:
                    issue.object_id = task_id
        match = re.match(r"nodes\[(\d+)\]", path)
        if match:
            index = int(match.group(1))
            if index < len(nodes):
                node_id = _str(nodes[index].get("task_id"))
                if node_id:
                    issue.object_id = node_id


def _structure_issues(document: Any, key: str, root_id: str) -> list[ValidationIssue]:
    issues = check_structure(document, _schema(key), object_id=root_id)
    _enrich_structure_ids(issues, document)
    return issues


# ---------------------------------------------------------------------------
# 规格 DesignSpec
# ---------------------------------------------------------------------------


def validate_design_spec(spec: Any, *, expected_version: int | None = None) -> ValidationReport:
    """校验设计规格的完整性与引用；供规格确认（V01–V03）使用。"""
    document = _plain(spec)
    doc = _as_dict(document)
    spec_id = _str(doc.get("spec_id")) or "SPEC"
    issues = _structure_issues(document, "design_spec", spec_id)
    issues.extend(_check_spec_semantics(doc, spec_id))
    if expected_version is not None and _int(doc.get("version")) != expected_version:
        issues.append(
            _issue(
                C.REVISION_MISMATCH,
                object_id=spec_id,
                field_path="version",
                message=(
                    f"规格当前版本为 {doc.get('version')}，本次确认基于版本 "
                    f"{expected_version}；请刷新最新版本后再确认（HTTP 409）"
                ),
                related_ids=[str(expected_version)],
                suggested_action="GET /specs/{spec_id} 获取最新版本后重新确认",
            )
        )
    target = ReportTarget(
        kind="design_spec",
        spec_id=spec_id if _str(doc.get("spec_id")) else None,
        version=_int(doc.get("version")),
    )
    return build_report(target=target, target_document=document, issues=issues)


def _check_spec_semantics(doc: Document, spec_id: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    requirements = _as_dict_list(doc.get("requirements"))
    requirement_index: dict[str, int] = {}
    for pos, requirement in enumerate(requirements):
        rid = _str(requirement.get("id"))
        if not rid:
            continue
        if rid in requirement_index:
            issues.append(
                _issue(
                    C.SPEC_REQUIREMENT_ID_DUPLICATE,
                    object_id=rid,
                    field_path=f"requirements[{pos}].id",
                    message=f"需求 ID {rid} 重复（首次出现在 requirements[{requirement_index[rid]}]）",
                    related_ids=[rid],
                    suggested_action="为需求分配稳定且唯一的 ID；旧案例可用 R1、R2 等编号",
                )
            )
        else:
            requirement_index[rid] = pos

    referenced_requirements: set[str] = set()
    modules = _as_dict_list(doc.get("modules"))
    module_index: dict[str, int] = {}
    for pos, module in enumerate(modules):
        mid = _str(module.get("id"))
        if not mid:
            continue
        if mid in module_index:
            issues.append(
                _issue(
                    C.SPEC_MODULE_ID_DUPLICATE,
                    object_id=mid,
                    field_path=f"modules[{pos}].id",
                    message=f"模块 ID {mid} 重复（首次出现在 modules[{module_index[mid]}]）",
                    related_ids=[mid],
                    suggested_action="为模块分配唯一 ID",
                )
            )
        else:
            module_index[mid] = pos
        for ref_pos, ref in enumerate(_as_list(module.get("requirement_ids"))):
            if not isinstance(ref, str):
                continue
            referenced_requirements.add(ref)
            if ref not in requirement_index:
                issues.append(
                    _issue(
                        C.SPEC_MODULE_REQUIREMENT_UNKNOWN,
                        object_id=mid,
                        field_path=f"modules[{pos}].requirement_ids[{ref_pos}]",
                        message=f"模块 {mid} 关联了不存在的需求 {ref}",
                        related_ids=[ref],
                        suggested_action="在 requirements 中补充该需求，或修正模块的需求引用",
                    )
                )

    design_items = _as_dict_list(doc.get("design_items"))
    item_index: dict[str, int] = {}
    for pos, item in enumerate(design_items):
        iid = _str(item.get("id"))
        if not iid:
            continue
        if iid in item_index:
            issues.append(
                _issue(
                    C.SPEC_DESIGN_ITEM_ID_DUPLICATE,
                    object_id=iid,
                    field_path=f"design_items[{pos}].id",
                    message=f"设计条目 ID {iid} 重复（首次出现在 design_items[{item_index[iid]}]）",
                    related_ids=[iid],
                    suggested_action="为设计条目分配唯一 ID",
                )
            )
        else:
            item_index[iid] = pos
        for ref_pos, ref in enumerate(_as_list(item.get("requirement_ids"))):
            if not isinstance(ref, str):
                continue
            referenced_requirements.add(ref)
            if ref not in requirement_index:
                issues.append(
                    _issue(
                        C.SPEC_DESIGN_ITEM_REQUIREMENT_UNKNOWN,
                        object_id=iid,
                        field_path=f"design_items[{pos}].requirement_ids[{ref_pos}]",
                        message=f"设计条目 {iid} 关联了不存在的需求 {ref}",
                        related_ids=[ref],
                        suggested_action="在 requirements 中补充该需求，或修正设计条目的需求引用",
                    )
                )

    constraints = _as_dict_list(doc.get("constraints"))
    constraint_index: dict[str, int] = {}
    for pos, constraint in enumerate(constraints):
        cid = _str(constraint.get("id"))
        if not cid:
            continue
        if cid in constraint_index:
            issues.append(
                _issue(
                    C.SPEC_CONSTRAINT_ID_DUPLICATE,
                    object_id=cid,
                    field_path=f"constraints[{pos}].id",
                    message=f"约束 ID {cid} 重复（首次出现在 constraints[{constraint_index[cid]}]）",
                    related_ids=[cid],
                    suggested_action="为约束分配唯一 ID",
                )
            )
        else:
            constraint_index[cid] = pos
        if _str(constraint.get("status")) == "unspecified":
            issues.append(
                _issue(
                    C.SPEC_CONSTRAINT_UNSPECIFIED,
                    object_id=cid,
                    field_path=f"constraints[{pos}].status",
                    message=f"约束 {cid} 标记为 unspecified：确认规格前需要补全，或改为 not_applicable",
                    related_ids=[cid],
                    suggested_action="补充约束内容；确实不适用时把 status 改为 not_applicable",
                    severity="warning",
                )
            )

    for rid, pos in requirement_index.items():
        if rid not in referenced_requirements:
            issues.append(
                _issue(
                    C.SPEC_REQUIREMENT_UNMAPPED,
                    object_id=rid,
                    field_path=f"requirements[{pos}]",
                    message=f"需求 {rid} 未被任何模块或设计条目引用，规格追溯不完整",
                    related_ids=[rid],
                    suggested_action="在 modules 或 design_items 中补充对需求 {rid} 的关联".format(rid=rid),
                    severity="warning",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# 任务计划 TaskPlan
# ---------------------------------------------------------------------------


def validate_task_plan(
    plan: Any,
    *,
    spec: Any,
    template: Any | None = None,
    expected_revision: int | None = None,
) -> ValidationReport:
    """校验任务计划；返回绑定 plan revision 与内容摘要的报告。"""
    document = _plain(plan)
    spec_document = _plain(spec)
    template_document = _plain(template) if template is not None else None
    doc = _as_dict(document)
    plan_id = _str(doc.get("plan_id")) or "PLAN"

    issues = _structure_issues(document, "task_plan", plan_id)
    indexes = _index_plan(doc)

    issues.extend(_check_plan_refs(doc, spec_document, template_document, plan_id))
    issues.extend(_check_task_refs(doc, indexes, spec_document, template_document))
    issues.extend(_check_task_ids(indexes))
    issues.extend(_check_outputs(indexes))
    issues.extend(_check_dependencies(indexes))
    issues.extend(_check_artifacts(indexes, spec_document))
    coverage_issues, coverage = _check_coverage(indexes, spec_document, template_document)
    issues.extend(coverage_issues)
    issues.extend(_check_config(doc, indexes, template_document, plan_id))
    if expected_revision is not None and _int(doc.get("revision")) != expected_revision:
        issues.append(
            _issue(
                C.REVISION_MISMATCH,
                object_id=plan_id,
                field_path="revision",
                message=(
                    f"计划当前修订为 {doc.get('revision')}，本次提交基于修订 "
                    f"{expected_revision}；请重新读取最新修订后再提交（HTTP 409）"
                ),
                related_ids=[str(expected_revision)],
                suggested_action="GET /plans/{plan_id} 获取最新 revision 后重新提交".format(
                    plan_id=plan_id
                ),
            )
        )

    target = ReportTarget(
        kind="task_plan",
        plan_id=plan_id if _str(doc.get("plan_id")) else None,
        revision=_int(doc.get("revision")),
    )
    return build_report(
        target=target, target_document=document, issues=issues, coverage=coverage
    )


def _index_plan(doc: Document) -> Document:
    tasks = _as_dict_list(doc.get("tasks"))
    tasks_by_id: dict[str, Document] = {}
    task_pos: dict[str, int] = {}
    for pos, task in enumerate(tasks):
        tid = _str(task.get("id"))
        if not tid or tid in tasks_by_id:
            continue
        tasks_by_id[tid] = task
        task_pos[tid] = pos
    outputs_by_task: dict[str, dict[str, tuple[int, int, Document]]] = {}
    for pos, task in enumerate(tasks):
        tid = _str(task.get("id"))
        if not tid:
            continue
        table = outputs_by_task.setdefault(tid, {})
        for out_pos, output in enumerate(_as_dict_list(task.get("outputs"))):
            oid = _str(output.get("id"))
            if oid and oid not in table:
                table[oid] = (pos, out_pos, output)
    return {
        "tasks": tasks,
        "tasks_by_id": tasks_by_id,
        "task_pos": task_pos,
        "outputs_by_task": outputs_by_task,
    }


def _check_plan_refs(
    doc: Document,
    spec_doc: Document,
    template_doc: Document | None,
    plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    spec_ref = _as_dict(doc.get("spec_ref"))
    actual_spec_id = _str(spec_doc.get("spec_id"))
    actual_spec_version = _int(spec_doc.get("version"))
    if _str(spec_ref.get("spec_id")) != actual_spec_id:
        issues.append(
            _issue(
                C.REF_SPEC_UNKNOWN,
                object_id=plan_id,
                field_path="spec_ref.spec_id",
                message=(
                    f"计划引用的规格 {spec_ref.get('spec_id')!r} 与当前规格 "
                    f"{actual_spec_id!r} 不一致"
                ),
                related_ids=[actual_spec_id],
                suggested_action="刷新规格引用后重新生成计划",
            )
        )
    if _int(spec_ref.get("version")) != actual_spec_version:
        issues.append(
            _issue(
                C.REF_VERSION_MISMATCH,
                object_id=plan_id,
                field_path="spec_ref.version",
                message=(
                    f"计划基于规格修订 {spec_ref.get('version')}，当前规格为 "
                    f"{actual_spec_version}；请基于最新修订重新生成计划"
                ),
                related_ids=[str(actual_spec_version)],
                suggested_action="重新读取规格最新版本并更新 spec_ref.version",
            )
        )
    if template_doc is not None:
        template_ref = _as_dict(doc.get("template_ref"))
        actual_template_id = _str(template_doc.get("template_id"))
        actual_template_version = _int(template_doc.get("version"))
        if _str(template_ref.get("template_id")) != actual_template_id:
            issues.append(
                _issue(
                    C.REF_TEMPLATE_UNKNOWN,
                    object_id=plan_id,
                    field_path="template_ref.template_id",
                    message=(
                        f"计划引用的模板 {template_ref.get('template_id')!r} 与传入模板 "
                        f"{actual_template_id!r} 不一致"
                    ),
                    related_ids=[actual_template_id],
                    suggested_action="GET /templates 获取可用模板并更新 template_ref",
                )
            )
        if _int(template_ref.get("version")) != actual_template_version:
            issues.append(
                _issue(
                    C.REF_VERSION_MISMATCH,
                    object_id=plan_id,
                    field_path="template_ref.version",
                    message=(
                        f"计划基于模板修订 {template_ref.get('version')}，当前模板为 "
                        f"{actual_template_version}"
                    ),
                    related_ids=[str(actual_template_version)],
                    suggested_action="更新 template_ref.version 后重新校验",
                )
            )
    return issues


def _ids_of(items: list[Document]) -> set[str]:
    return {_str(item.get("id")) for item in items if _str(item.get("id"))}


def _check_task_refs(
    doc: Document,
    indexes: Document,
    spec_doc: Document,
    template_doc: Document | None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    requirement_ids = _ids_of(_as_dict_list(spec_doc.get("requirements")))
    module_ids = _ids_of(_as_dict_list(spec_doc.get("modules")))
    design_ids = _ids_of(_as_dict_list(spec_doc.get("design_items")))
    tasks: list[Document] = indexes["tasks"]

    allowed_roles: list[str] = []
    stages: list[str] = []
    if template_doc is not None:
        allowed_roles = [r for r in _as_list(template_doc.get("allowed_roles")) if isinstance(r, str)]
        stages = [s for s in _as_list(template_doc.get("stages")) if isinstance(s, str)]

    for pos, task in enumerate(tasks):
        tid = _str(task.get("id")) or f"tasks[{pos}]"
        for ref_pos, ref in enumerate(_as_list(task.get("requirement_ids"))):
            if isinstance(ref, str) and ref not in requirement_ids:
                issues.append(
                    _issue(
                        C.REF_REQUIREMENT_UNKNOWN,
                        object_id=tid,
                        field_path=f"tasks[{pos}].requirement_ids[{ref_pos}]",
                        message=f"任务 {tid} 引用了不存在的需求 {ref}",
                        related_ids=[ref],
                        suggested_action="在规格中补充该需求，或修正任务的 requirement_ids",
                    )
                )
        for ref_pos, ref in enumerate(_as_list(task.get("module_ids"))):
            if isinstance(ref, str) and ref not in module_ids:
                issues.append(
                    _issue(
                        C.REF_MODULE_UNKNOWN,
                        object_id=tid,
                        field_path=f"tasks[{pos}].module_ids[{ref_pos}]",
                        message=f"任务 {tid} 引用了不存在的模块 {ref}",
                        related_ids=[ref],
                        suggested_action="在规格 modules 中补充该模块，或修正任务的 module_ids",
                    )
                )
        design_refs = [r for r in _as_list(task.get("design_refs")) if isinstance(r, str)]
        for ref_pos, ref in enumerate(design_refs):
            if ref not in design_ids:
                issues.append(
                    _issue(
                        C.REF_DESIGN_UNKNOWN,
                        object_id=tid,
                        field_path=f"tasks[{pos}].design_refs[{ref_pos}]",
                        message=f"任务 {tid} 引用了不存在的设计条目 {ref}",
                        related_ids=[ref],
                        suggested_action="在规格 design_items 中补充该条目，或修正 design_refs",
                    )
                )
        if not design_refs and _str(task.get("kind")) != "support":
            issues.append(
                _issue(
                    C.TASK_DESIGN_REF_EMPTY,
                    object_id=tid,
                    field_path=f"tasks[{pos}].design_refs",
                    message=f"任务 {tid} 未引用任何设计条目，规格追溯不完整",
                    related_ids=[tid],
                    suggested_action="在 design_refs 中补充本任务依据的设计条目",
                    severity="warning",
                )
            )
        role = _str(task.get("role"))
        if allowed_roles and role not in allowed_roles:
            issues.append(
                _issue(
                    C.TASK_ROLE_NOT_ALLOWED,
                    object_id=tid,
                    field_path=f"tasks[{pos}].role",
                    message=f"任务 {tid} 的角色 {role!r} 不在模板允许角色 {allowed_roles} 中",
                    related_ids=[role],
                    suggested_action="改用模板允许的角色，或更新模板 allowed_roles",
                )
            )
        stage = _str(task.get("stage"))
        if stages and stage not in stages:
            issues.append(
                _issue(
                    C.TASK_STAGE_NOT_IN_TEMPLATE,
                    object_id=tid,
                    field_path=f"tasks[{pos}].stage",
                    message=f"任务 {tid} 的阶段 {stage!r} 不在模板阶段 {stages} 中",
                    related_ids=[stage],
                    suggested_action="改用模板允许的阶段，或更新模板 stages",
                )
            )
    return issues


def _check_task_ids(indexes: Document) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    first_seen: dict[str, int] = {}
    for pos, task in enumerate(indexes["tasks"]):
        tid = _str(task.get("id"))
        if not tid:
            continue
        if tid in first_seen:
            issues.append(
                _issue(
                    C.TASK_ID_DUPLICATE,
                    object_id=tid,
                    field_path=f"tasks[{pos}].id",
                    message=f"任务 ID {tid} 重复（首次出现在 tasks[{first_seen[tid]}]）",
                    related_ids=[tid],
                    suggested_action="为任务分配唯一 ID；重复定义时只有首次出现的任务参与校验",
                )
            )
        else:
            first_seen[tid] = pos
    return issues


def _check_outputs(indexes: Document) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: dict[str, tuple[str, int, int]] = {}
    for pos, task in enumerate(indexes["tasks"]):
        tid = _str(task.get("id"))
        if not tid:
            continue
        for out_pos, output in enumerate(_as_dict_list(task.get("outputs"))):
            oid = _str(output.get("id"))
            if not oid:
                continue
            if oid in seen:
                first_tid, first_pos, first_out_pos = seen[oid]
                issues.append(
                    _issue(
                        C.OUTPUT_ID_DUPLICATE,
                        object_id=tid,
                        field_path=f"tasks[{pos}].outputs[{out_pos}].id",
                        message=(
                            f"产物 ID {oid} 重复：已由任务 {first_tid} 在 "
                            f"tasks[{first_pos}].outputs[{first_out_pos}] 声明"
                        ),
                        related_ids=[oid, first_tid],
                        suggested_action="产物 ID 必须在整个计划内唯一，引用时写作 <task_id>/<output_id>",
                    )
                )
            else:
                seen[oid] = (tid, pos, out_pos)
    return issues


def _dependency_pairs(indexes: Document) -> list[tuple[str, str, str]]:
    """返回去重后的 (前置任务, 当前任务, 依赖字段路径)。"""
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    pairs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pos, task in enumerate(indexes["tasks"]):
        tid = _str(task.get("id"))
        if not tid:
            continue
        for dep_pos, dep in enumerate(_as_dict_list(task.get("depends_on"))):
            did = _str(dep.get("task_id"))
            if not did or did == tid or did not in tasks_by_id:
                continue
            if (did, tid) in seen:
                continue
            seen.add((did, tid))
            pairs.append((did, tid, f"tasks[{pos}].depends_on[{dep_pos}].task_id"))
    return pairs


def _check_dependencies(indexes: Document) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    for pos, task in enumerate(indexes["tasks"]):
        tid = _str(task.get("id"))
        if not tid:
            continue
        seen_deps: set[str] = set()
        for dep_pos, dep in enumerate(_as_dict_list(task.get("depends_on"))):
            did = _str(dep.get("task_id"))
            if not did:
                continue
            path = f"tasks[{pos}].depends_on[{dep_pos}].task_id"
            if did == tid:
                issues.append(
                    _issue(
                        C.DEPENDENCY_SELF,
                        object_id=tid,
                        field_path=path,
                        message=f"任务 {tid} 不能依赖自身",
                        related_ids=[tid],
                        suggested_action="删除该自依赖边",
                    )
                )
            elif did not in tasks_by_id:
                issues.append(
                    _issue(
                        C.DEPENDENCY_UNKNOWN,
                        object_id=tid,
                        field_path=path,
                        message=f"任务 {tid} 的前置任务 {did} 不存在",
                        related_ids=[did],
                        suggested_action="补充该前置任务，或删除该依赖；校验器不会自动删边",
                    )
                )
            if did in seen_deps:
                issues.append(
                    _issue(
                        C.DEPENDENCY_DUPLICATE,
                        object_id=tid,
                        field_path=path,
                        message=f"任务 {tid} 重复声明了对 {did} 的依赖",
                        related_ids=[did],
                        suggested_action="删除重复的依赖声明",
                    )
                )
            seen_deps.add(did)

    pairs = _dependency_pairs(indexes)
    cycle = _find_cycle(indexes["tasks"], pairs)
    if cycle:
        cycle_path = cycle + [cycle[0]]
        first = cycle[0]
        last = cycle[-1]
        back_edge_path = "depends_on"
        last_task = tasks_by_id.get(last)
        if last_task is not None:
            for dep_pos, dep in enumerate(_as_dict_list(last_task.get("depends_on"))):
                if _str(dep.get("task_id")) == first:
                    back_edge_path = f"tasks[{indexes['task_pos'][last]}].depends_on[{dep_pos}].task_id"
                    break
        issues.append(
            _issue(
                C.DEPENDENCY_CYCLE,
                object_id=first,
                field_path=back_edge_path,
                message=(
                    f"任务依赖存在闭合环：{' → '.join(cycle_path)}"
                    "（箭头表示依赖方向：A → B 表示 A 依赖 B；边方向为前置 → 当前任务）"
                ),
                related_ids=list(cycle_path),
                suggested_action=(
                    "删除环中的一条依赖边后重新校验；受环影响的下游任务暂不编排，"
                    "校验器不会自动删边"
                ),
            )
        )
    return issues


def _find_cycle(
    tasks: list[Document], pairs: list[tuple[str, str, str]]
) -> list[str] | None:
    adjacency: dict[str, list[str]] = {}
    for from_task, to_task, _ in pairs:
        adjacency.setdefault(to_task, []).append(from_task)
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def dfs(tid: str) -> list[str] | None:
        visited.add(tid)
        stack.append(tid)
        on_stack.add(tid)
        for dep in adjacency.get(tid, []):
            if dep in on_stack:
                start = stack.index(dep)
                return stack[start:]
            if dep not in visited:
                found = dfs(dep)
                if found:
                    return found
        stack.pop()
        on_stack.discard(tid)
        return None

    for task in tasks:
        tid = _str(task.get("id"))
        if not tid or tid in visited:
            continue
        found = dfs(tid)
        if found:
            return found
    return None


def _check_artifacts(indexes: Document, spec_doc: Document) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    design_ids = _ids_of(_as_dict_list(spec_doc.get("design_items")))
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    outputs_by_task: dict[str, dict[str, tuple[int, int, Document]]] = indexes["outputs_by_task"]

    for pos, task in enumerate(indexes["tasks"]):
        tid = _str(task.get("id"))
        if not tid:
            continue
        direct_deps = {
            _str(dep.get("task_id")) for dep in _as_dict_list(task.get("depends_on"))
        }
        for input_pos, input_ref in enumerate(_as_dict_list(task.get("inputs"))):
            source = _str(input_ref.get("source"))
            ref = _str(input_ref.get("ref"))
            input_type = _str(input_ref.get("type"))
            path = f"tasks[{pos}].inputs[{input_pos}]"
            if source == "design":
                if design_ids and ref not in design_ids:
                    issues.append(
                        _issue(
                            C.ARTIFACT_DESIGN_REF_UNKNOWN,
                            object_id=tid,
                            field_path=f"{path}.ref",
                            message=f"任务 {tid} 的输入引用了不存在的设计条目 {ref}",
                            related_ids=[ref],
                            suggested_action="修正 inputs.ref，或先在规格 design_items 中补充该条目",
                        )
                    )
                if input_type and input_type != "design_contract":
                    issues.append(
                        _issue(
                            C.ARTIFACT_TYPE_MISMATCH,
                            object_id=tid,
                            field_path=f"{path}.type",
                            message=(
                                f"source=design 的输入 {ref} 类型应为 design_contract，"
                                f"实际为 {input_type}"
                            ),
                            related_ids=[ref, input_type],
                            suggested_action="把该输入 type 改为 design_contract",
                        )
                    )
            elif source == "task_output":
                parts = ref.split("/")
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    issues.append(
                        _issue(
                            C.ARTIFACT_INPUT_REF_INVALID,
                            object_id=tid,
                            field_path=f"{path}.ref",
                            message=(
                                f"任务 {tid} 的任务产物引用 {ref!r} 格式非法；"
                                "应写作 <task_id>/<output_id>"
                            ),
                            related_ids=[ref],
                            suggested_action="把 inputs.ref 改为 <task_id>/<output_id> 形式",
                        )
                    )
                    continue
                producer, output_id = parts
                if producer == tid:
                    issues.append(
                        _issue(
                            C.ARTIFACT_SELF_REFERENCE,
                            object_id=tid,
                            field_path=f"{path}.ref",
                            message=f"任务 {tid} 不能把自身产物 {output_id} 作为输入",
                            related_ids=[producer, output_id],
                            suggested_action="改为引用其他任务的产物，或删除该输入",
                        )
                    )
                    continue
                if producer not in tasks_by_id:
                    issues.append(
                        _issue(
                            C.ARTIFACT_PRODUCER_UNKNOWN,
                            object_id=tid,
                            field_path=f"{path}.ref",
                            message=(
                                f"任务 {tid} 的输入引用了不存在的生产任务 {producer}"
                                f"（引用 {ref}）"
                            ),
                            related_ids=[producer, output_id],
                            suggested_action="修正 inputs.ref 中的任务 ID，或补充该生产任务",
                        )
                    )
                    continue
                table = outputs_by_task.get(producer, {})
                entry = table.get(output_id)
                if entry is None:
                    issues.append(
                        _issue(
                            C.ARTIFACT_OUTPUT_UNKNOWN,
                            object_id=tid,
                            field_path=f"{path}.ref",
                            message=(
                                f"任务 {tid} 的输入引用了 {producer}/{output_id}，"
                                f"但任务 {producer} 未声明产物 {output_id}"
                            ),
                            related_ids=[producer, output_id],
                            suggested_action=(
                                f"核对任务 {producer} 的 outputs，或修正 inputs.ref；"
                                "编译/确认前必须解决"
                            ),
                        )
                    )
                    continue
                producer_pos, producer_out_pos, output = entry
                producer_type = _str(output.get("type"))
                if input_type and producer_type and input_type != producer_type:
                    issues.append(
                        _issue(
                            C.ARTIFACT_TYPE_MISMATCH,
                            object_id=tid,
                            field_path=f"{path}.type",
                            message=(
                                f"输入类型 {input_type} 与生产者 {producer} 的产物类型 "
                                f"{producer_type} 不一致"
                                f"（tasks[{producer_pos}].outputs[{producer_out_pos}]）"
                            ),
                            related_ids=[ref, producer_type],
                            suggested_action="按生产者声明的产物类型修正 inputs.type",
                        )
                    )
                if producer not in direct_deps:
                    issues.append(
                        _issue(
                            C.ARTIFACT_PRODUCER_DEPENDENCY_MISSING,
                            object_id=tid,
                            field_path=f"tasks[{pos}].depends_on",
                            message=(
                                f"任务 {tid} 直接消费 {producer} 的产物，但依赖中没有"
                                f"声明对生产任务 {producer} 的直接依赖"
                            ),
                            related_ids=[producer],
                            suggested_action=(
                                f"在 depends_on 中添加 "
                                f'{{"task_id": "{producer}", "reason": "消费其产物"}}'
                            ),
                        )
                    )
    return issues


def _check_coverage(
    indexes: Document,
    spec_doc: Document,
    template_doc: Document | None,
) -> tuple[list[ValidationIssue], Coverage | None]:
    issues: list[ValidationIssue] = []
    entries: list[RequirementCoverage] = []
    require_verification = True
    if template_doc is not None:
        require_verification = bool(template_doc.get("require_verification", True))

    for req_pos, requirement in enumerate(_as_dict_list(spec_doc.get("requirements"))):
        rid = _str(requirement.get("id"))
        if not rid:
            continue
        implementation_ids: list[str] = []
        verification_ids: list[str] = []
        for task in indexes["tasks"]:
            tid = _str(task.get("id"))
            if not tid:
                continue
            requirement_ids = [r for r in _as_list(task.get("requirement_ids")) if isinstance(r, str)]
            if rid not in requirement_ids:
                continue
            kind = _str(task.get("kind"))
            if kind == "implementation":
                implementation_ids.append(tid)
            elif kind == "verification":
                verification_ids.append(tid)
        if not implementation_ids:
            issues.append(
                _issue(
                    C.COVERAGE_IMPLEMENTATION_MISSING,
                    object_id=rid,
                    field_path=f"spec.requirements[{req_pos}]",
                    message=f"需求 {rid} 没有实现任务，无法确认已被实现",
                    related_ids=verification_ids,
                    suggested_action=(
                        f"为需求 {rid} 补充 kind=implementation 的任务，"
                        "或修正相关任务的 requirement_ids"
                    ),
                )
            )
        if not verification_ids:
            severity = "error" if require_verification else "warning"
            issues.append(
                _issue(
                    C.COVERAGE_VERIFICATION_MISSING,
                    object_id=rid,
                    field_path=f"spec.requirements[{req_pos}]",
                    message=(
                        f"需求 {rid} 没有验证任务，无法确认验收口径被覆盖"
                        + ("" if require_verification else "（模板未强制要求验证任务）")
                    ),
                    related_ids=implementation_ids,
                    suggested_action=(
                        f"为需求 {rid} 补充 kind=verification 的任务，"
                        "或修正相关任务的 requirement_ids"
                    ),
                    severity=severity,
                )
            )
        if implementation_ids and verification_ids:
            status = "covered"
        elif implementation_ids:
            status = "missing_verification"
        elif verification_ids:
            status = "missing_implementation"
        else:
            status = "missing_both"
        entries.append(
            RequirementCoverage(
                requirement_id=rid,
                implementation_task_ids=implementation_ids,
                verification_task_ids=verification_ids,
                status=status,
            )
        )

    coverage: Coverage | None = None
    if entries:
        total = len(entries)
        implementation_ratio = round(
            sum(1 for entry in entries if entry.implementation_task_ids) / total, 4
        )
        verification_ratio = round(
            sum(1 for entry in entries if entry.verification_task_ids) / total, 4
        )
        coverage = Coverage(
            requirements=entries,
            implementation_ratio=implementation_ratio,
            verification_ratio=verification_ratio,
        )
    return issues, coverage


def _check_config(
    doc: Document,
    indexes: Document,
    template_doc: Document | None,
    plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    config = _as_dict(doc.get("config"))
    capacities = _as_dict(config.get("role_capacity"))
    allowed_roles: list[str] = []
    if template_doc is not None:
        allowed_roles = [r for r in _as_list(template_doc.get("allowed_roles")) if isinstance(r, str)]
    for role in capacities:
        if allowed_roles and role not in allowed_roles:
            issues.append(
                _issue(
                    C.CONFIG_ROLE_UNKNOWN,
                    object_id=plan_id,
                    field_path=f"config.role_capacity.{role}",
                    message=f"容量配置包含模板未允许的角色 {role!r}",
                    related_ids=[role],
                    suggested_action="删除该角色容量，或把角色加入模板 allowed_roles",
                )
            )
    used_roles: list[str] = []
    for task in indexes["tasks"]:
        role = _str(task.get("role"))
        if role and role not in used_roles:
            used_roles.append(role)
    for role in used_roles:
        if role not in capacities:
            issues.append(
                _issue(
                    C.CONFIG_ROLE_CAPACITY_MISSING,
                    object_id=plan_id,
                    field_path="config.role_capacity",
                    message=f"任务使用了角色 {role!r}，但 config.role_capacity 未配置该角色容量",
                    related_ids=[role],
                    suggested_action=f"在 role_capacity 中增加 {role!r} 的正整数容量",
                )
            )
    ordering = [k for k in _as_list(config.get("ordering")) if isinstance(k, str)]
    if ordering and "task_id" not in ordering:
        issues.append(
            _issue(
                C.CONFIG_ORDERING_NONDETERMINISTIC,
                object_id=plan_id,
                field_path="config.ordering",
                message="排序键缺少 task_id：同优先级任务的顺序可能不稳定（V07 要求相同输入编排一致）",
                related_ids=ordering,
                suggested_action='在 ordering 末尾追加 "task_id" 作为稳定兜底排序键',
                severity="warning",
            )
        )
    return issues


# ---------------------------------------------------------------------------
# 执行计划 ExecutionPlan
# ---------------------------------------------------------------------------


def validate_execution_plan(
    execution_plan: Any,
    *,
    task_plan: Any,
    validation_report: Any | None = None,
    spec: Any | None = None,
    template: Any | None = None,
) -> ValidationReport:
    """校验执行计划；可与任务计划、校验报告、规格/模板摘要交叉核对。"""
    document = _plain(execution_plan)
    plan_document = _plain(task_plan)
    doc = _as_dict(document)
    plan_doc = _as_dict(plan_document)
    execution_plan_id = _str(doc.get("execution_plan_id")) or "EXEC"

    issues = _structure_issues(document, "execution_plan", execution_plan_id)
    indexes = _index_plan(plan_doc)
    pairs = _dependency_pairs(indexes)
    plan_ref = _as_dict(doc.get("plan_ref"))
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    config = _as_dict(plan_doc.get("config"))
    max_concurrency = _int(config.get("max_concurrency"))
    role_capacity = _as_dict(config.get("role_capacity"))

    if _str(plan_ref.get("plan_id")) != _str(plan_doc.get("plan_id")) or _int(
        plan_ref.get("revision")
    ) != _int(plan_doc.get("revision")):
        issues.append(
            _issue(
                C.EXEC_PLAN_REF_MISMATCH,
                object_id=execution_plan_id,
                field_path="plan_ref",
                message=(
                    f"执行计划引用 {plan_ref.get('plan_id')!r}@{plan_ref.get('revision')}，"
                    f"但传入的任务计划为 {plan_doc.get('plan_id')!r}@{plan_doc.get('revision')}"
                ),
                related_ids=[_str(plan_doc.get("plan_id"))],
                suggested_action="基于当前任务修订重新编译执行计划",
            )
        )

    spec_document = _plain(spec) if spec is not None else None
    template_document = _plain(template) if template is not None else None
    if spec_document is not None and _str(doc.get("spec_digest")) != content_digest(
        spec_document
    ):
        issues.append(
            _issue(
                C.EXEC_SOURCE_DIGEST_MISMATCH,
                object_id=execution_plan_id,
                field_path="spec_digest",
                message="spec_digest 与当前规格内容摘要不一致，执行计划已过期",
                related_ids=[_str(_as_dict(spec_document).get("spec_id"))],
                suggested_action="规格已变化：重新校验并编译计划",
            )
        )
    if template_document is not None and _str(doc.get("template_digest")) != content_digest(
        template_document
    ):
        issues.append(
            _issue(
                C.EXEC_SOURCE_DIGEST_MISMATCH,
                object_id=execution_plan_id,
                field_path="template_digest",
                message="template_digest 与当前模板内容摘要不一致，执行计划已过期",
                related_ids=[_str(_as_dict(template_document).get("template_id"))],
                suggested_action="模板已变化：重新校验并编译计划",
            )
        )
    if _str(doc.get("config_digest")) != content_digest(config):
        issues.append(
            _issue(
                C.EXEC_SOURCE_DIGEST_MISMATCH,
                object_id=execution_plan_id,
                field_path="config_digest",
                message="config_digest 与任务计划 config 的内容摘要不一致，执行计划已过期",
                related_ids=[_str(plan_doc.get("plan_id"))],
                suggested_action="重新编译执行计划",
            )
        )

    issues.extend(_check_exec_nodes(doc, indexes, execution_plan_id))
    issues.extend(_check_exec_edges(doc, indexes, pairs, execution_plan_id))
    level_issues, level_of = _check_exec_levels(doc, indexes, pairs, execution_plan_id)
    issues.extend(level_issues)
    issues.extend(_check_exec_batches(doc, indexes, pairs, config, max_concurrency, execution_plan_id))
    issues.extend(_check_exec_bindings(doc, indexes, config, max_concurrency, execution_plan_id))
    issues.extend(
        _check_exec_validation_ref(
            doc,
            plan_doc,
            validation_report,
            execution_plan_id,
        )
    )

    target = ReportTarget(
        kind="execution_plan",
        execution_plan_id=execution_plan_id if _str(doc.get("execution_plan_id")) else None,
        plan_id=_str(plan_ref.get("plan_id")) or None,
        revision=_int(plan_ref.get("revision")),
    )
    return build_report(target=target, target_document=document, issues=issues)


def _check_exec_nodes(
    doc: Document, indexes: Document, execution_plan_id: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    nodes = _as_dict_list(doc.get("nodes"))
    seen: dict[str, int] = {}
    for pos, node in enumerate(nodes):
        nid = _str(node.get("task_id"))
        if not nid:
            continue
        if nid in seen:
            issues.append(
                _issue(
                    C.EXEC_NODE_DUPLICATE,
                    object_id=nid,
                    field_path=f"nodes[{pos}].task_id",
                    message=f"执行节点 {nid} 重复（首次出现在 nodes[{seen[nid]}]）",
                    related_ids=[nid],
                    suggested_action="每个任务只应有一个执行节点",
                )
            )
            continue
        seen[nid] = pos
        task = tasks_by_id.get(nid)
        if task is None:
            issues.append(
                _issue(
                    C.EXEC_NODE_UNKNOWN,
                    object_id=nid,
                    field_path=f"nodes[{pos}]",
                    message=f"执行节点 {nid} 在任务计划中不存在",
                    related_ids=[nid],
                    suggested_action="删除该节点，或重新编译执行计划",
                )
            )
            continue
        mismatches: list[str] = []
        if _str(node.get("role")) != _str(task.get("role")):
            mismatches.append(f"role {node.get('role')!r} != {task.get('role')!r}")
        if _str(node.get("stage")) != _str(task.get("stage")):
            mismatches.append(f"stage {node.get('stage')!r} != {task.get('stage')!r}")
        if _int(node.get("priority")) != _int(task.get("priority")):
            mismatches.append(f"priority {node.get('priority')!r} != {task.get('priority')!r}")
        if mismatches:
            issues.append(
                _issue(
                    C.EXEC_NODE_MISMATCH,
                    object_id=nid,
                    field_path=f"nodes[{pos}]",
                    message=f"执行节点 {nid} 与任务卡不一致：" + "；".join(mismatches),
                    related_ids=[nid],
                    suggested_action="执行节点必须是任务卡字段的派生结果，重新编译",
                )
            )
    for tid in tasks_by_id:
        if tid not in seen:
            issues.append(
                _issue(
                    C.EXEC_NODE_MISSING,
                    object_id=tid,
                    field_path="nodes",
                    message=f"任务 {tid} 没有对应的执行节点",
                    related_ids=[tid],
                    suggested_action="每个任务必须安排一次；重新编译执行计划",
                )
            )
    return issues


def _check_exec_edges(
    doc: Document,
    indexes: Document,
    pairs: list[tuple[str, str, str]],
    execution_plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    expected = {(from_task, to_task) for from_task, to_task, _ in pairs}
    actual: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pos, edge in enumerate(_as_dict_list(doc.get("edges"))):
        from_task = _str(edge.get("from_task"))
        to_task = _str(edge.get("to_task"))
        path = f"edges[{pos}]"
        if from_task not in tasks_by_id or to_task not in tasks_by_id:
            issues.append(
                _issue(
                    C.EXEC_EDGE_UNKNOWN_TASK,
                    object_id=from_task or to_task or execution_plan_id,
                    field_path=path,
                    message=(
                        f"边 {from_task} → {to_task} 的端点不在任务计划中"
                    ),
                    related_ids=[from_task, to_task],
                    suggested_action="修正边的端点，或重新编译执行计划",
                )
            )
            continue
        if (from_task, to_task) in seen:
            issues.append(
                _issue(
                    C.EXEC_EDGE_DUPLICATE,
                    object_id=from_task,
                    field_path=path,
                    message=f"边 {from_task} → {to_task} 重复声明",
                    related_ids=[from_task, to_task],
                    suggested_action="删除重复边",
                )
            )
            continue
        seen.add((from_task, to_task))
        actual.append((from_task, to_task))
        if (from_task, to_task) not in expected:
            if (to_task, from_task) in expected:
                issues.append(
                    _issue(
                        C.EXEC_EDGE_DIRECTION_REVERSED,
                        object_id=from_task,
                        field_path=path,
                        message=(
                            f"边方向错误：任务 {to_task} 依赖 {from_task}，"
                            f"应记录为 from_task={to_task}, to_task={from_task}（前置 → 当前）"
                        ),
                        related_ids=[from_task, to_task],
                        suggested_action="边的方向必须为前置任务 → 依赖它的当前任务",
                    )
                )
            else:
                issues.append(
                    _issue(
                        C.EXEC_EDGE_UNKNOWN,
                        object_id=from_task,
                        field_path=path,
                        message=f"边 {from_task} → {to_task} 在任务计划的 depends_on 中不存在",
                        related_ids=[from_task, to_task],
                        suggested_action="执行计划的边必须由 depends_on 归一化得到",
                    )
                )
    actual_set = set(actual)
    for from_task, to_task, _ in pairs:
        if (from_task, to_task) not in actual_set:
            issues.append(
                _issue(
                    C.EXEC_EDGE_MISSING,
                    object_id=to_task,
                    field_path="edges",
                    message=f"缺少依赖边 {from_task} → {to_task}",
                    related_ids=[from_task, to_task],
                    suggested_action="每条 depends_on 都必须归一化为一条边",
                )
            )
    return issues


def _check_exec_levels(
    doc: Document,
    indexes: Document,
    pairs: list[tuple[str, str, str]],
    execution_plan_id: str,
) -> tuple[list[ValidationIssue], dict[str, int]]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    level_of: dict[str, int] = {}
    for level_pos, level in enumerate(_as_list(doc.get("topological_levels"))):
        if not isinstance(level, list):
            continue
        if not level:
            issues.append(
                _issue(
                    C.EXEC_LEVEL_EMPTY,
                    object_id=execution_plan_id,
                    field_path=f"topological_levels[{level_pos}]",
                    message=f"拓扑层 {level_pos} 为空",
                    related_ids=[],
                    suggested_action="重新生成拓扑分层",
                )
            )
        for item_pos, item in enumerate(level):
            if not isinstance(item, str):
                continue
            if item in level_of:
                issues.append(
                    _issue(
                        C.EXEC_LEVEL_TASK_DUPLICATE,
                        object_id=item,
                        field_path=f"topological_levels[{level_pos}][{item_pos}]",
                        message=f"任务 {item} 在拓扑分层中重复出现（首次在第 {level_of[item]} 层）",
                        related_ids=[item],
                        suggested_action="每个任务在拓扑分层中只能出现一次",
                    )
                )
                continue
            level_of[item] = level_pos
            if item not in tasks_by_id:
                issues.append(
                    _issue(
                        C.EXEC_LEVEL_TASK_UNKNOWN,
                        object_id=item,
                        field_path=f"topological_levels[{level_pos}][{item_pos}]",
                        message=f"拓扑分层中的任务 {item} 不存在",
                        related_ids=[item],
                        suggested_action="重新生成拓扑分层",
                    )
                )
    for tid in tasks_by_id:
        if tid not in level_of:
            issues.append(
                _issue(
                    C.EXEC_LEVEL_TASK_MISSING,
                    object_id=tid,
                    field_path="topological_levels",
                    message=f"任务 {tid} 未出现在任何拓扑层",
                    related_ids=[tid],
                    suggested_action="每个任务必须出现一次；重新生成拓扑分层",
                )
            )
    for from_task, to_task, _ in pairs:
        from_level = level_of.get(from_task)
        to_level = level_of.get(to_task)
        if from_level is None or to_level is None:
            continue
        if from_level >= to_level:
            issues.append(
                _issue(
                    C.EXEC_LEVEL_ORDER,
                    object_id=to_task,
                    field_path=f"topological_levels[{to_level}]",
                    message=(
                        f"任务 {to_task} 位于第 {to_level} 层，其前置 {from_task} 位于第 "
                        f"{from_level} 层；前置必须位于更早的层"
                    ),
                    related_ids=[from_task, to_task],
                    suggested_action="重新生成拓扑分层，保证前置任务层号更小",
                )
            )
    return issues, level_of


def _check_exec_batches(
    doc: Document,
    indexes: Document,
    pairs: list[tuple[str, str, str]],
    config: Document,
    max_concurrency: int | None,
    execution_plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    role_capacity = _as_dict(config.get("role_capacity"))
    batch_of: dict[str, int] = {}
    for batch_pos, batch in enumerate(_as_list(doc.get("batches"))):
        if not isinstance(batch, list):
            continue
        if not batch:
            issues.append(
                _issue(
                    C.EXEC_BATCH_EMPTY,
                    object_id=execution_plan_id,
                    field_path=f"batches[{batch_pos}]",
                    message=f"批次 {batch_pos} 为空",
                    related_ids=[],
                    suggested_action="删除空批次或重新生成批次",
                )
            )
        if max_concurrency is not None and len(batch) > max_concurrency:
            issues.append(
                _issue(
                    C.EXEC_BATCH_OVER_CAPACITY,
                    object_id=execution_plan_id,
                    field_path=f"batches[{batch_pos}]",
                    message=(
                        f"批次 {batch_pos} 包含 {len(batch)} 个任务，超过全局并发 "
                        f"{max_concurrency}"
                    ),
                    related_ids=[item for item in batch if isinstance(item, str)],
                    suggested_action="缩小批次规模或提升 max_concurrency",
                )
            )
        role_counts: dict[str, int] = {}
        for item_pos, item in enumerate(batch):
            if not isinstance(item, str):
                continue
            if item in batch_of:
                issues.append(
                    _issue(
                        C.EXEC_BATCH_TASK_DUPLICATE,
                        object_id=item,
                        field_path=f"batches[{batch_pos}][{item_pos}]",
                        message=f"任务 {item} 重复出现在批次中（首次在批次 {batch_of[item]}）",
                        related_ids=[item],
                        suggested_action="每个任务只能安排一次",
                    )
                )
                continue
            batch_of[item] = batch_pos
            if item not in tasks_by_id:
                issues.append(
                    _issue(
                        C.EXEC_BATCH_TASK_UNKNOWN,
                        object_id=item,
                        field_path=f"batches[{batch_pos}][{item_pos}]",
                        message=f"批次中的任务 {item} 不存在",
                        related_ids=[item],
                        suggested_action="重新生成批次",
                    )
                )
                continue
            role = _str(tasks_by_id[item].get("role"))
            role_counts[role] = role_counts.get(role, 0) + 1
        for role, count in role_counts.items():
            capacity = _int(role_capacity.get(role))
            if capacity is not None and count > capacity:
                issues.append(
                    _issue(
                        C.EXEC_BATCH_ROLE_CAPACITY,
                        object_id=execution_plan_id,
                        field_path=f"batches[{batch_pos}]",
                        message=(
                            f"批次 {batch_pos} 中角色 {role!r} 有 {count} 个任务，"
                            f"超过角色容量 {capacity}"
                        ),
                        related_ids=[item for item in batch if isinstance(item, str)],
                        suggested_action="按角色容量拆分批次，或调整 config.role_capacity",
                    )
                )
    for tid in tasks_by_id:
        if tid not in batch_of:
            issues.append(
                _issue(
                    C.EXEC_BATCH_TASK_MISSING,
                    object_id=tid,
                    field_path="batches",
                    message=f"任务 {tid} 未被安排到任何批次",
                    related_ids=[tid],
                    suggested_action="每个任务必须安排一次；重新编译执行计划",
                )
            )
    for from_task, to_task, _ in pairs:
        from_batch = batch_of.get(from_task)
        to_batch = batch_of.get(to_task)
        if from_batch is None or to_batch is None:
            continue
        if from_batch >= to_batch:
            issues.append(
                _issue(
                    C.EXEC_BATCH_DEPENDENCY,
                    object_id=to_task,
                    field_path=f"batches[{to_batch}]",
                    message=(
                        f"任务 {to_task} 位于批次 {to_batch}，其前置 {from_task} 位于批次 "
                        f"{from_batch}；前置必须位于更早的批次"
                    ),
                    related_ids=[from_task, to_task],
                    suggested_action="重新编排批次，保证依赖边前置任务在更早批次",
                )
            )
    return issues


def _check_exec_bindings(
    doc: Document,
    indexes: Document,
    config: Document,
    max_concurrency: int | None,
    execution_plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    tasks_by_id: dict[str, Document] = indexes["tasks_by_id"]
    role_capacity = _as_dict(config.get("role_capacity"))
    bindings = _as_dict_list(doc.get("bindings"))
    binding_index: dict[str, tuple[int, Document]] = {}
    for pos, binding in enumerate(bindings):
        tid = _str(binding.get("task_id"))
        if not tid:
            continue
        if tid in binding_index:
            issues.append(
                _issue(
                    C.EXEC_BINDING_DUPLICATE,
                    object_id=tid,
                    field_path=f"bindings[{pos}].task_id",
                    message=f"任务 {tid} 重复绑定（首次出现在 bindings[{binding_index[tid][0]}]）",
                    related_ids=[tid],
                    suggested_action="每个任务只应有一条绑定记录",
                )
            )
            continue
        binding_index[tid] = (pos, binding)
        task = tasks_by_id.get(tid)
        if task is None:
            issues.append(
                _issue(
                    C.EXEC_BINDING_UNKNOWN,
                    object_id=tid,
                    field_path=f"bindings[{pos}]",
                    message=f"绑定记录中的任务 {tid} 不存在",
                    related_ids=[tid],
                    suggested_action="删除该绑定或重新编译",
                )
            )
            continue
        global_slot = _int(binding.get("global_slot"))
        if (
            global_slot is None
            or global_slot < 1
            or (max_concurrency is not None and global_slot > max_concurrency)
        ):
            issues.append(
                _issue(
                    C.EXEC_BINDING_GLOBAL_SLOT,
                    object_id=tid,
                    field_path=f"bindings[{pos}].global_slot",
                    message=(
                        f"任务 {tid} 的全局槽位 {binding.get('global_slot')} 超出 "
                        f"1..{max_concurrency} 范围"
                    ),
                    related_ids=[tid],
                    suggested_action="全局槽位必须落在 1..max_concurrency 内",
                )
            )
        role = _str(task.get("role"))
        capacity = _int(role_capacity.get(role))
        role_slot = _int(binding.get("role_slot"))
        if (
            role_slot is None
            or role_slot < 1
            or (capacity is not None and role_slot > capacity)
        ):
            issues.append(
                _issue(
                    C.EXEC_BINDING_ROLE_SLOT,
                    object_id=tid,
                    field_path=f"bindings[{pos}].role_slot",
                    message=(
                        f"任务 {tid}（角色 {role!r}）的角色槽位 {binding.get('role_slot')} "
                        f"超出 1..{capacity} 范围"
                    ),
                    related_ids=[tid, role],
                    suggested_action="角色槽位必须落在 1..role_capacity[role] 内",
                )
            )
    for tid in tasks_by_id:
        if tid not in binding_index:
            issues.append(
                _issue(
                    C.EXEC_BINDING_MISSING,
                    object_id=tid,
                    field_path="bindings",
                    message=f"任务 {tid} 缺少槽位绑定",
                    related_ids=[tid],
                    suggested_action="每个任务必须绑定全局槽位与角色槽位",
                )
            )
    batch_of: dict[str, int] = {}
    for batch_pos, batch in enumerate(_as_list(doc.get("batches"))):
        if not isinstance(batch, list):
            continue
        global_slots: dict[int, str] = {}
        role_slots: dict[tuple[str, int], str] = {}
        for item in batch:
            if not isinstance(item, str):
                continue
            batch_of.setdefault(item, batch_pos)
            entry = binding_index.get(item)
            if entry is None:
                continue
            pos, binding = entry
            global_slot = _int(binding.get("global_slot"))
            if global_slot is not None:
                other = global_slots.get(global_slot)
                if other is not None:
                    issues.append(
                        _issue(
                            C.EXEC_BINDING_SLOT_CONFLICT,
                            object_id=item,
                            field_path=f"bindings[{pos}].global_slot",
                            message=(
                                f"批次 {batch_pos} 内任务 {item} 与 {other} 占用同一全局槽位 "
                                f"{global_slot}"
                            ),
                            related_ids=[item, other],
                            suggested_action="同一批次内全局槽位不能重复",
                        )
                    )
                else:
                    global_slots[global_slot] = item
            task = tasks_by_id.get(item)
            role = _str(task.get("role")) if task else ""
            role_slot = _int(binding.get("role_slot"))
            if role and role_slot is not None:
                key = (role, role_slot)
                other = role_slots.get(key)
                if other is not None:
                    issues.append(
                        _issue(
                            C.EXEC_BINDING_SLOT_CONFLICT,
                            object_id=item,
                            field_path=f"bindings[{pos}].role_slot",
                            message=(
                                f"批次 {batch_pos} 内角色 {role!r} 的任务 {item} 与 {other} "
                                f"占用同一角色槽位 {role_slot}"
                            ),
                            related_ids=[item, other],
                            suggested_action="同一批次内同一角色的槽位不能重复",
                        )
                    )
                else:
                    role_slots[key] = item
    return issues


def _check_exec_validation_ref(
    doc: Document,
    plan_doc: Document,
    validation_report: Any | None,
    execution_plan_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if validation_report is None:
        return issues
    report = _plain(validation_report)
    report_doc = _as_dict(report)
    plan_ref = _as_dict(doc.get("plan_ref"))
    target = _as_dict(report_doc.get("target"))
    report_plan_id = _str(target.get("plan_id"))
    report_revision = _int(target.get("revision"))
    if (
        _str(target.get("kind")) != "task_plan"
        or report_plan_id != _str(plan_ref.get("plan_id"))
        or report_revision != _int(plan_ref.get("revision"))
    ):
        issues.append(
            _issue(
                C.EXEC_VALIDATION_REF_MISMATCH,
                object_id=execution_plan_id,
                field_path="validation_ref",
                message=(
                    "校验报告绑定的对象与执行计划的 plan_ref 不一致："
                    f"报告为 {target.get('kind')!r}/{report_plan_id}@{report_revision}"
                ),
                related_ids=[report_plan_id],
                suggested_action="使用同一修订的校验报告编译执行计划",
            )
        )
    if report_doc.get("blocked") is True:
        issues.append(
            _issue(
                C.EXEC_VALIDATION_BLOCKED,
                object_id=execution_plan_id,
                field_path="validation_ref",
                message="校验报告 blocked=true：存在 error 级问题，禁止编译/确认",
                related_ids=[],
                suggested_action="修复报告中的 error 后重新校验",
            )
        )
    expected_digest = content_digest(report)
    stored_digest = _str(_as_dict(doc.get("validation_ref")).get("report_digest"))
    if stored_digest != expected_digest:
        issues.append(
            _issue(
                C.EXEC_VALIDATION_STALE,
                object_id=execution_plan_id,
                field_path="validation_ref.report_digest",
                message=(
                    "validation_ref.report_digest 与提供的校验报告内容摘要不一致；"
                    "计划或报告已变更，属于陈旧报告"
                ),
                related_ids=[],
                suggested_action="重新校验计划，再用新报告重新编译执行计划",
            )
        )
    return issues


__all__ = [
    "validate_design_spec",
    "validate_task_plan",
    "validate_execution_plan",
]
