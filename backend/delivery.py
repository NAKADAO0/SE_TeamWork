import json
from contextlib import closing
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .app.contracts.models import SCHEMA_VERSION, content_digest
from .app.contracts.spec_intake import normalize_spec_dict
from .app.orchestration.compiler import compile_plan as compile_with_orchestrator
from .app.validation.validator import validate_task_plan
from .workflow import resolve_template, transaction


class Revision(BaseModel):
    revision: int = Field(ge=1)


class CompileRequest(Revision):
    config: dict[str, Any] | None = None


class Confirmation(Revision):
    execution_plan_id: str


def _dep_ids(task: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for dep in task.get("depends_on") or []:
        if isinstance(dep, str) and dep.strip():
            out.append(dep.strip())
        elif isinstance(dep, dict):
            tid = str(dep.get("task_id") or dep.get("task") or dep.get("id") or "").strip()
            if tid:
                out.append(tid)
    return out


def _find_cycle(tasks: list[dict[str, Any]]) -> list[str] | None:
    adj = {str(t["id"]): _dep_ids(t) for t in tasks if t.get("id")}
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in adj}
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for nxt in adj.get(node, []):
            if nxt not in color:
                continue
            if color[nxt] == GREY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for tid in list(adj):
        if color[tid] == WHITE:
            found = dfs(tid)
            if found:
                return found
    return None


def _can_run_strict_schema_validation(plan: dict[str, Any], spec: dict[str, Any]) -> bool:
    if plan.get("schema_version") != SCHEMA_VERSION:
        return False
    if not spec.get("modules") or not spec.get("design_items") or not spec.get("constraints"):
        return False
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False
    required_task_keys = {
        "id", "title", "description", "requirement_ids", "module_ids", "design_refs",
        "kind", "stage", "role", "inputs", "outputs", "acceptance", "depends_on",
        "priority", "estimated_effort",
    }
    return all(isinstance(t, dict) and required_task_keys.issubset(t.keys()) for t in tasks)


def _strict_task_plan_doc(plan: dict[str, Any], pid: str, revision: int, spec: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    task_fields = (
        "id", "title", "description", "requirement_ids", "module_ids", "design_refs",
        "kind", "stage", "role", "inputs", "outputs", "acceptance", "depends_on",
        "priority", "estimated_effort",
    )
    clean_tasks = []
    for t in plan.get("tasks") or []:
        item = {k: t[k] for k in task_fields if k in t}
        if isinstance(item.get("acceptance"), str):
            item["acceptance"] = [item["acceptance"]] if item["acceptance"].strip() else []
        deps = []
        for dep in item.get("depends_on") or []:
            if isinstance(dep, str):
                deps.append({"task_id": dep, "reason": "前置任务依赖"})
            elif isinstance(dep, dict):
                deps.append({"task_id": str(dep.get("task_id") or ""), "reason": str(dep.get("reason") or "前置依赖")})
        item["depends_on"] = deps
        clean_tasks.append(item)

    spec_ref = dict(plan.get("spec_ref") or {})
    if not spec_ref.get("spec_id"):
        spec_ref["spec_id"] = spec.get("spec_id") or "SPEC-1"
    if not spec_ref.get("version"):
        spec_ref["version"] = spec.get("version") or 1

    template_ref = dict(plan.get("template_ref") or {})
    if not template_ref.get("template_id"):
        template_ref["template_id"] = template.get("template_id") or "default"
    if isinstance(template_ref.get("version"), str):
        template_ref["version"] = int(float(template_ref["version"]))
    elif not template_ref.get("version"):
        template_ref["version"] = int(template.get("version") or 1)

    cfg = dict(plan.get("config") or {})
    if "max_concurrency" not in cfg:
        cfg["max_concurrency"] = int(plan.get("capacity") or template.get("max_concurrency") or 2)
    if "role_capacity" not in cfg or not cfg["role_capacity"]:
        cfg["role_capacity"] = dict(template.get("role_capacity") or {"backend": 1, "frontend": 1, "qa": 1})
    if "ordering" not in cfg or not cfg["ordering"]:
        cfg["ordering"] = list(template.get("ordering") or ["priority", "task_id"])

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": str(plan.get("plan_id") or pid),
        "revision": int(revision),
        "spec_ref": spec_ref,
        "template_ref": template_ref,
        "config": cfg,
        "generation_mode": str(plan.get("generation_mode") or "offline_rule"),
        "tasks": clean_tasks,
        "assumptions": list(plan.get("assumptions") or []),
        "clarifications": list(plan.get("clarifications") or []),
    }


def _validate_legacy_or_partial(
    plan: dict[str, Any],
    spec: dict[str, Any],
    template: dict[str, Any],
    pid: str,
    revision: int,
) -> dict[str, Any]:
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    tasks = plan.get("tasks") or []

    def add_issue(severity: str, code: str, object_id: str, field_path: str, message: str, related: list[str] | None = None, action: str = ""):
        item = {
            "severity": severity,
            "code": code,
            "object_id": object_id,
            "field_path": field_path,
            "message": message,
            "related_ids": related or [],
            "suggested_action": action,
        }
        (blocking if severity == "error" else warnings).append(item)

    if not isinstance(tasks, list) or not tasks:
        add_issue("error", "TASK_LIST_EMPTY", pid, "tasks", "任务列表为空", [], "至少保留一个可执行任务")
        return {"blocking": blocking, "warnings": warnings, "coverage": None}

    by_id: dict[str, dict[str, Any]] = {}
    outputs_by_task: dict[str, set[str]] = {}
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            add_issue("error", "INVALID_TASK", f"tasks[{idx}]", f"tasks[{idx}]", f"tasks[{idx}] 不是对象")
            continue
        tid = str(task.get("id") or "").strip()
        title = str(task.get("title") or "").strip()
        role = str(task.get("role") or "").strip()
        acc = task.get("acceptance")
        acc_items = [str(x).strip() for x in acc if str(x).strip()] if isinstance(acc, list) else ([str(acc).strip()] if isinstance(acc, str) and acc.strip() else [])

        if not tid:
            add_issue("error", "MISSING_TASK_ID", f"tasks[{idx}]", f"tasks[{idx}].id", f"tasks[{idx}] 缺少编号")
            continue
        if tid in by_id:
            add_issue("error", "DUPLICATE_TASK_ID", tid, f"tasks[{idx}].id", f"任务编号 {tid} 重复", [tid])
        by_id[tid] = task
        outputs_by_task[tid] = {str(o["id"]) for o in (task.get("outputs") or []) if isinstance(o, dict) and o.get("id")}

        if not title:
            add_issue("error", "MISSING_TITLE", tid, f"tasks[{idx}].title", f"任务 {tid} 缺少目标标题", [tid])
        if not role:
            add_issue("error", "MISSING_ROLE", tid, f"tasks[{idx}].role", f"任务 {tid} 缺少角色", [tid])
        if not acc_items:
            add_issue("error", "MISSING_ACCEPTANCE", tid, f"tasks[{idx}].acceptance", f"任务 {tid} 缺少可检查的验收条件", [tid], "补写验收条件")
        if not isinstance(task.get("depends_on"), list):
            add_issue("error", "INVALID_DEPENDS_ON", tid, f"tasks[{idx}].depends_on", f"tasks[{idx}].depends_on 应为数组", [tid])

    design_ids = {str(d["id"]) for d in (spec.get("design_items") or []) if isinstance(d, dict) and d.get("id")}
    req_ids_in_spec = [str(r["id"]) for r in (spec.get("requirements") or []) if isinstance(r, dict) and r.get("id")]

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict) or not task.get("id"):
            continue
        tid = str(task["id"])
        deps = _dep_ids(task) if isinstance(task.get("depends_on"), list) else []
        for dep in deps:
            if dep == tid:
                add_issue("error", "SELF_DEPENDENCY", tid, f"tasks[{idx}].depends_on", f"任务 {tid} 依赖自己", [tid], "删除自依赖")
            elif dep not in by_id:
                add_issue("error", "MISSING_DEPENDENCY", tid, f"tasks[{idx}].depends_on", f"前置任务 {dep} 不存在", [dep], "补建前置任务或移除依赖")

        for rid in task.get("requirement_ids") or []:
            if req_ids_in_spec and rid not in req_ids_in_spec:
                add_issue("error", "REF_REQUIREMENT_UNKNOWN", tid, f"tasks[{idx}].requirement_ids", f"任务 {tid} 引用了不存在的需求 {rid}", [rid])

        for inp in task.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            if inp.get("source") == "task_output":
                ref = str(inp.get("ref") or "")
                parts = ref.split("/", 1)
                prod_id = parts[0] if len(parts) == 2 else ""
                out_id = parts[1] if len(parts) == 2 else ""
                if not prod_id or out_id not in outputs_by_task.get(prod_id, set()):
                    add_issue("error", "UNRESOLVED_INPUT", tid, f"tasks[{idx}].inputs", f"输入 {ref} 找不到生产任务或产物", [prod_id] if prod_id else [])
                elif prod_id not in deps:
                    add_issue("error", "MISSING_PRODUCER_DEPENDENCY", tid, f"tasks[{idx}].depends_on", f"任务 {tid} 消费了 {ref} 但未声明对 {prod_id} 的前置依赖", [prod_id])
            elif inp.get("source") == "design" and design_ids and inp.get("ref") not in design_ids:
                add_issue("error", "UNRESOLVED_DESIGN_REF", tid, f"tasks[{idx}].inputs", f"设计引用 {inp.get('ref')} 不在规格中", [])

    cycle = _find_cycle([t for t in tasks if isinstance(t, dict) and t.get("id")])
    if cycle:
        add_issue("error", "DEPENDENCY_CYCLE", cycle[0], "tasks", f"存在依赖环：{' → '.join(cycle)}", cycle, "移除环上任意一条依赖边")

    # 当任务带 kind 且规格有需求时检查需求覆盖率
    has_kinds = any(isinstance(t, dict) and t.get("kind") for t in tasks)
    coverage = None
    if req_ids_in_spec and has_kinds:
        req_entries = []
        impl_covered = 0
        ver_covered = 0
        for rid in req_ids_in_spec:
            rel = [t for t in tasks if isinstance(t, dict) and rid in (t.get("requirement_ids") or [])]
            impl_ids = [t["id"] for t in rel if t.get("kind") == "implementation"]
            ver_ids = [t["id"] for t in rel if t.get("kind") == "verification"]
            if impl_ids:
                impl_covered += 1
            else:
                add_issue("error", "REQUIREMENT_WITHOUT_IMPLEMENTATION", rid, f"requirements[{rid}]", f"需求 {rid} 没有实现任务", [t["id"] for t in rel], "补充实现任务")
            if template.get("require_verification", True):
                if ver_ids:
                    ver_covered += 1
                else:
                    add_issue("error", "REQUIREMENT_WITHOUT_VERIFICATION", rid, f"requirements[{rid}]", f"需求 {rid} 没有验证任务", [t["id"] for t in rel], "补充验证任务")
            status = "covered" if (impl_ids and ver_ids) else ("missing_both" if not impl_ids and not ver_ids else ("missing_implementation" if not impl_ids else "missing_verification"))
            req_entries.append({
                "requirement_id": rid,
                "implementation_task_ids": impl_ids,
                "verification_task_ids": ver_ids,
                "status": status,
            })
        total = len(req_ids_in_spec)
        coverage = {
            "requirements": req_entries,
            "implementation_ratio": impl_covered / total,
            "verification_ratio": ver_covered / total,
        }

    return {"blocking": blocking, "warnings": warnings, "coverage": coverage}


def validate_plan_document(plan: dict[str, Any], pid: str, revision: int) -> dict[str, Any]:
    """统一校验入口：完整 TaskPlan 走成员 3 权威校验器，简版/草稿走兼容校验。"""
    raw_spec = plan.get("spec") or {}
    norm_spec = plan.get("normalized_spec") or (normalize_spec_dict(raw_spec) if isinstance(raw_spec, dict) and raw_spec else {})
    template = plan.get("template") or resolve_template((plan.get("template_ref") or {}).get("template_id") if isinstance(plan.get("template_ref"), dict) else plan.get("template_ref")) or resolve_template("default")

    if _can_run_strict_schema_validation(plan, norm_spec):
        strict_plan = _strict_task_plan_doc(plan, pid, revision, norm_spec, template)
        strict_spec = {**norm_spec, "spec_id": strict_plan["spec_ref"]["spec_id"], "version": strict_plan["spec_ref"]["version"]}
        strict_template = {
            "schema_version": SCHEMA_VERSION,
            "template_id": str(strict_plan["template_ref"]["template_id"]),
            "version": int(strict_plan["template_ref"]["version"]),
            "name": template.get("name", "模板"),
            "description": template.get("description", ""),
            "stages": list(template.get("stages") or ["design", "implement", "verify", "integrate"]),
            "allowed_roles": list(template.get("allowed_roles") or ["backend", "frontend", "qa"]),
            "require_verification": bool(template.get("require_verification", True)),
        }
        report = validate_task_plan(strict_plan, spec=strict_spec, template=strict_template)
        blocking = [
            {
                "severity": "error",
                "code": i.code,
                "object_id": i.object_id,
                "field_path": i.field_path,
                "message": i.message,
                "related_ids": list(i.related_ids),
                "suggested_action": i.suggested_action,
            }
            for i in report.issues if i.severity == "error"
        ]
        warnings = [
            {
                "severity": "warning",
                "code": i.code,
                "object_id": i.object_id,
                "field_path": i.field_path,
                "message": i.message,
                "related_ids": list(i.related_ids),
                "suggested_action": i.suggested_action,
            }
            for i in report.issues if i.severity == "warning"
        ]
        coverage = None
        if report.coverage is not None:
            coverage = {
                "requirements": [vars(r) for r in report.coverage.requirements],
                "implementation_ratio": report.coverage.implementation_ratio,
                "verification_ratio": report.coverage.verification_ratio,
            }
        return {
            "blocking": blocking,
            "warnings": warnings,
            "coverage": coverage,
            "report_digest": report.content_digest,
        }

    res = _validate_legacy_or_partial(plan, norm_spec, template, pid, revision)
    return {
        **res,
        "report_digest": content_digest({"plan_id": pid, "revision": revision, "tasks": plan.get("tasks", [])}),
    }


def validate_demo(plan):
    res = _validate_legacy_or_partial(plan, normalize_spec_dict(plan.get("spec") or {}), resolve_template("default"), str(plan.get("plan_id") or "PLAN"), int(plan.get("revision") or 1))
    return [item["message"] for item in res["blocking"]]


def delivery_router(store, validator=None, compiler=None):
    router = APIRouter()

    def current(db, pid, revision):
        row = db.execute("SELECT revision FROM plans WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "计划不存在")
        if row[0] != revision:
            raise HTTPException(409, detail={"current_revision": row[0]})

    def document(pid, revision):
        try:
            return store.read(pid, revision)["plan"]
        except KeyError:
            raise HTTPException(404, "计划或修订不存在")

    def lookup(db, pid, revision, kind):
        row = db.execute("SELECT body FROM artifacts WHERE plan_id=? AND revision=? AND kind=?",
                         (pid, revision, kind)).fetchone()
        return json.loads(row[0]) if row else None

    def record(db, pid, revision, kind, value):
        db.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?)", (pid, revision, kind, json.dumps(value, ensure_ascii=False)))

    @router.post("/plans/{pid}/validate")
    def validate(pid: str, request: Revision):
        plan = document(pid, request.revision)
        if validator is not None:
            raw_errors = validator(plan)
            blocking = [
                {"severity": "error", "code": "VALIDATION_ERROR", "object_id": pid, "field_path": "tasks", "message": str(e), "related_ids": [], "suggested_action": ""}
                for e in raw_errors
            ]
            warnings = []
            coverage = None
            report_digest = content_digest(plan)
        else:
            outcome = validate_plan_document(plan, pid, request.revision)
            blocking = outcome["blocking"]
            warnings = outcome["warnings"]
            coverage = outcome["coverage"]
            report_digest = outcome["report_digest"]
            raw_errors = [item["message"] for item in blocking]

        with transaction(store) as db:
            current(db, pid, request.revision)
            report = lookup(db, pid, request.revision, "validation")
            if report is None:
                report = {
                    "validation_ref": uuid4().hex,
                    "report_digest": report_digest,
                    "plan_id": pid,
                    "revision": request.revision,
                    "plan_ref": {"plan_id": pid, "revision": request.revision},
                    "errors": raw_errors,
                    "blocking": blocking,
                    "warnings": warnings,
                    "coverage": coverage,
                }
                record(db, pid, request.revision, "validation", report)
        return report

    @router.post("/plans/{pid}/compile")
    def compile_plan_route(pid: str, request: CompileRequest):
        report = validate(pid, Revision(revision=request.revision))
        if report["errors"]:
            raise HTTPException(422, detail=report)
        plan = document(pid, request.revision)

        with transaction(store) as db:
            current(db, pid, request.revision)
            existing_exec = lookup(db, pid, request.revision, "execution")
            confirmed = lookup(db, pid, request.revision, "confirmation")

        if existing_exec is not None and request.config is None:
            return existing_exec

        if confirmed is not None and request.config is not None:
            if existing_exec and existing_exec.get("config") != request.config:
                raise HTTPException(409, "当前修订已确认，请派生新草稿后再调整容量配置")
            if existing_exec:
                return existing_exec

        if compiler is not None:
            batches = compiler(plan)
            compiled = {
                "compiled": True,
                "execution_plan_id": uuid4().hex,
                "validation_ref": report["validation_ref"],
                "plan_id": pid,
                "revision": request.revision,
                "plan_ref": {"plan_id": pid, "revision": request.revision},
                "batches": batches,
                "nodes": [t["id"] for t in plan["tasks"]],
                "edges": [{"from": dep if isinstance(dep, str) else dep["task_id"], "to": t["id"]} for t in plan["tasks"] for dep in t["depends_on"]],
                "blocked": [],
            }
            compiled["plan_ref"]["execution_plan_id"] = compiled["execution_plan_id"]
        else:
            try:
                compiled = compile_with_orchestrator(
                    {**plan, "plan_id": pid, "revision": request.revision},
                    spec=plan.get("normalized_spec") or plan.get("spec"),
                    template=plan.get("template"),
                    validation_report=report,
                    config_override=request.config,
                )
            except ValueError as exc:
                raise HTTPException(422, detail=str(exc))

            # 若配置发生变化导致此前已有执行计划与新配置一致可直接复用，否则保存最新编排快照
            compiled["validation_ref"] = report["validation_ref"]

        with transaction(store) as db:
            current(db, pid, request.revision)
            existing_exec = lookup(db, pid, request.revision, "execution")
            # Compilation runs outside the write transaction. Confirmation may have won the race.
            if lookup(db, pid, request.revision, "confirmation") is not None:
                if existing_exec and (request.config is None or existing_exec.get("config") == compiled.get("config")):
                    return existing_exec
                raise HTTPException(409, "当前修订已确认，请派生新草稿后再调整容量配置")
            if existing_exec is None or (request.config is not None and existing_exec.get("config") != compiled.get("config")):
                record(db, pid, request.revision, "execution", compiled)
                # 同步把更新后的 config 保存在未确认的修订文档内（不改变 tasks/revision）
                if request.config is not None and compiled.get("config"):
                    updated_plan = {**plan, "config": compiled["config"], "capacity": compiled["config"]["max_concurrency"]}
                    db.execute("UPDATE revisions SET document=? WHERE plan_id=? AND revision=?",
                               (json.dumps(updated_plan, ensure_ascii=False), pid, request.revision))
            else:
                compiled = existing_exec
        return compiled

    @router.post("/plans/{pid}/confirm")
    def confirm(pid: str, request: Confirmation):
        with transaction(store) as db:
            current(db, pid, request.revision)
            validation = lookup(db, pid, request.revision, "validation")
            if validation and validation.get("errors"):
                raise HTTPException(422, "校验仍有阻断错误，无法确认")
            execution = lookup(db, pid, request.revision, "execution")
            if not execution or execution.get("execution_plan_id") != request.execution_plan_id or execution.get("compiled") is False:
                raise HTTPException(409, "需要本修订的有效编排结果")
            if not lookup(db, pid, request.revision, "confirmation"):
                record(db, pid, request.revision, "confirmation", request.model_dump())
        return {
            "plan_id": pid,
            "revision": request.revision,
            "execution_plan_id": request.execution_plan_id,
            "status": "confirmed",
        }

    @router.get("/plans/{pid}/export")
    def export(pid: str, revision: int = Query(ge=1), format: Literal["json", "markdown"] = "json"):
        plan = document(pid, revision)
        with closing(store.connect()) as db:
            db.execute("BEGIN")
            artifacts = {kind: json.loads(body) for kind, body in db.execute(
                "SELECT kind,body FROM artifacts WHERE plan_id=? AND revision=? AND kind IN ('confirmation','execution','validation')",
                (pid, revision))}
        if "confirmation" not in artifacts:
            raise HTTPException(409, "请先确认当前修订")
        bundle = {"plan_id": pid, "revision": revision, "status": "confirmed", "plan": plan, **artifacts}
        raw = json.dumps(bundle, ensure_ascii=False, indent=2)
        if format == "markdown":
            def fmt_acc(val):
                return "；".join(str(x) for x in val) if isinstance(val, list) else str(val or "")
            tasks_md = "\n".join(
                f"- {t['id']} {t['title']}（{t['role']}）：{fmt_acc(t.get('acceptance'))}"
                for t in plan["tasks"]
            )
            batches_md = "\n".join(
                f"{i}. {', '.join(batch)}"
                for i, batch in enumerate(artifacts["execution"]["batches"], 1)
            )
            raw_spec = plan.get("spec") or plan.get("normalized_spec") or {}
            prod = raw_spec.get("product", "开发计划")
            title = prod.get("name", "开发计划") if isinstance(prod, dict) else prod
            raw = f"# {title}\n\n修订 {revision}\n\n## 任务与验收\n\n{tasks_md}\n\n## 执行批次\n\n{batches_md}\n\n## 完整快照\n\n{raw}\n"
        return Response(
            raw,
            media_type="application/json" if format == "json" else "text/markdown",
            headers={"Content-Disposition": f'attachment; filename="plan-r{revision}.{"json" if format == "json" else "md"}"'},
        )

    return router
