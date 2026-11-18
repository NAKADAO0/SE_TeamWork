"""生成主流程：预检 → 调用 → 校验 → 最多一次修复 → 返回四种状态之一。"""
import hashlib
import json
import time
import uuid

from . import fields as F
from .models import GenerationResult, PlanIssue, ProviderError, Trace
from .prompt import build_messages, build_repair_messages
from .providers import Provider
from .rules import errors, precheck, validate

# 给成员 5 的作业状态映射，直接照抄即可
JOB_STATUS = {
    "ok": "succeeded",
    "needs_clarification": "needs_clarification",
    "invalid_output": "invalid_output",
    "failed": "failed",
}

MAX_ATTEMPTS = 2  # 首次 + 一次修复，对应 V03


def _int(value, default=1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def spec_id_of(spec: dict) -> str:
    """规格没有 spec_id 时，用内容摘要生成一个稳定的编号，保证同输入同编号。"""
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return "SPEC-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def _sanitize_tasks(tasks: list, top_assumptions: list, top_clarifications: list) -> list:
    """只剥离模型误放到 task 内部的顶层字段及规范化 design 输入的产物类型别名，不捏造业务内容。"""
    allowed_task_keys = {F.out(F.TASK, k) for k in F.TASK}
    cleaned = []
    for raw_t in tasks:
        if not isinstance(raw_t, dict):
            cleaned.append(raw_t)
            continue
        t = dict(raw_t)
        # 若模型把 assumptions/clarifications 放到了任务内部，提到顶层并移除多余键
        extra_assump = t.pop("assumptions", None)
        if isinstance(extra_assump, list):
            for a in extra_assump:
                if isinstance(a, str) and a.strip() and a not in top_assumptions:
                    top_assumptions.append(a)
        extra_clar = t.pop("clarifications", None)
        if isinstance(extra_clar, list):
            for c in extra_clar:
                if isinstance(c, dict) and c not in top_clarifications:
                    top_clarifications.append(c)

        # 规范化输入引用：source=design 的 type 统一对齐契约枚举 design_contract
        if isinstance(t.get("inputs"), list):
            norm_inputs = []
            for inp in t["inputs"]:
                if isinstance(inp, dict):
                    item = {k: inp[k] for k in ("source", "ref", "type") if k in inp}
                    if item.get("source") == "design":
                        item["type"] = "design_contract"
                    norm_inputs.append(item)
                else:
                    norm_inputs.append(inp)
            t["inputs"] = norm_inputs

        if isinstance(t.get("outputs"), list):
            t["outputs"] = [
                {k: out[k] for k in ("id", "type", "description") if k in out}
                if isinstance(out, dict) else out
                for out in t["outputs"]
            ]

        if isinstance(t.get("depends_on"), list):
            t["depends_on"] = [
                {k: dep[k] for k in ("task_id", "reason") if k in dep}
                if isinstance(dep, dict) else dep
                for dep in t["depends_on"]
            ]

        cleaned.append({k: v for k, v in t.items() if k in allowed_task_keys})
    return cleaned


def envelope(plan: dict, spec: dict, template: dict, config: dict,
             mode: str, trace_id: str) -> dict:
    """补上成员 3 的 TaskPlan schema 要求的外层字段。

    schema 见 backend/app/contracts/schema/task_plan.schema.json（main 分支）。
    """
    if not isinstance(plan, dict):
        return plan

    roles = template.get("allowed_roles") or ["backend"]
    raw_tasks = F.pick(plan, F.PLAN, "tasks", []) or []
    assumptions = list(F.pick(plan, F.PLAN, "assumptions", []) or [])
    clarifications = list(F.pick(plan, F.PLAN, "clarifications", []) or [])
    tasks = _sanitize_tasks(raw_tasks, assumptions, clarifications)

    return {
        F.out(F.PLAN, "schema_version"): F.SCHEMA_VERSION,
        F.out(F.PLAN, "plan_id"): str(F.pick(plan, F.PLAN, "plan_id", "") or f"PLAN-{trace_id}"),
        F.out(F.PLAN, "revision"): _int(F.pick(plan, F.PLAN, "revision", 1)),
        F.out(F.PLAN, "spec_ref"): {
            "spec_id": str(spec.get("spec_id") or spec_id_of(spec)),
            "version": _int(spec.get("version"), 1),
        },
        F.out(F.PLAN, "template_ref"): {
            "template_id": str(template.get("template_id") or "default"),
            "version": _int(template.get("version"), 1),
        },
        F.out(F.PLAN, "config"): {
            "max_concurrency": _int(config.get("max_concurrency"), 2),
            "role_capacity": config.get("role_capacity") or {r: 1 for r in roles},
            "ordering": config.get("ordering") or ["priority", "task_id"],
        },
        F.out(F.PLAN, "generation_mode"): mode,
        F.out(F.PLAN, "tasks"): tasks,
        F.out(F.PLAN, "assumptions"): assumptions,
        F.out(F.PLAN, "clarifications"): clarifications,
    }


def _parse(raw: str):
    """容忍被 markdown 围栏包裹的输出；剥离后仍要过校验。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text), None
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1]), None
            except ValueError:
                pass
    return None, PlanIssue("E001", "error", "输出不是合法 JSON")


def _validate_with_contracts(plan: dict, spec: dict, template: dict) -> list[PlanIssue]:
    """当输入为完整契约 DesignSpec 时，叠加成员 3 权威校验器以确保零契约误差。"""
    if (
        spec.get("schema_version") != F.SCHEMA_VERSION
        or not spec.get("modules")
        or not spec.get("design_items")
        or not spec.get("constraints")
    ):
        return []
    try:
        from ..validation.validator import validate_task_plan
    except Exception:
        return []
    tpl_doc = {
        "schema_version": F.SCHEMA_VERSION,
        "template_id": str(template.get("template_id") or "default"),
        "version": _int(template.get("version"), 1),
        "name": str(template.get("name") or "默认模板"),
        "description": str(template.get("description") or "工作流模板"),
        "stages": list(template.get("stages") or ["design", "implement", "verify", "integrate"]),
        "allowed_roles": list(template.get("allowed_roles") or ["backend", "frontend", "qa"]),
        "require_verification": bool(template.get("require_verification", True)),
    }
    report = validate_task_plan(plan, spec=spec, template=tpl_doc)
    return [
        PlanIssue(
            code=i.code,
            severity=i.severity,
            message=i.message,
            object_id=i.object_id,
            field_path=i.field_path,
        )
        for i in report.issues
    ]


def _run_validation(plan: dict, spec: dict, template: dict) -> list[PlanIssue]:
    issues = validate(plan, spec, template)
    existing = {(i.severity, i.field_path, i.message) for i in issues}
    for extra in _validate_with_contracts(plan, spec, template):
        key = (extra.severity, extra.field_path, extra.message)
        if key not in existing:
            existing.add(key)
            issues.append(extra)
    return issues


def generate(
    spec: dict,
    template: dict,
    config: dict,
    provider: Provider,
    trace_id: str | None = None,
) -> GenerationResult:
    started = time.time()
    trace = Trace(
        mode=provider.name,
        model=str(getattr(provider, "model", "") or ""),
        trace_id=trace_id or uuid.uuid4().hex[:12],
    )

    # 1. 规格不够用就直接说，不调模型（可复现，不依赖模型心情）
    gaps = precheck(spec)
    if gaps:
        trace.attempts = 0
        trace.latency_ms = int((time.time() - started) * 1000)
        return GenerationResult("needs_clarification", gaps=gaps, trace=trace)

    messages = build_messages(spec, template, config)

    # 2. 首次调用
    try:
        raw = provider.complete(messages)
    except ProviderError as e:
        trace.attempts = 1
        trace.latency_ms = int((time.time() - started) * 1000)
        return GenerationResult("failed", issues=[PlanIssue("E900", "error", str(e))], trace=trace)

    trace.attempts = 1
    plan, parse_issue = _parse(raw)
    if plan is not None:
        plan = envelope(plan, spec, template, config, provider.name, trace.trace_id)
    issues = [parse_issue] if parse_issue else _run_validation(plan, spec, template)

    # 3. 有 error 才修复；只有 warning 直接通过
    if errors(issues):
        messages = build_repair_messages(spec, template, config, raw, errors(issues))
        try:
            raw2 = provider.complete(messages)
            trace.attempts = 2
        except ProviderError as e:
            trace.latency_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                "failed",
                issues=[PlanIssue("E900", "error", "修复调用失败：" + str(e))],
                trace=trace,
                rejected_output=raw,
            )

        plan2, parse_issue2 = _parse(raw2)
        if plan2 is not None:
            plan2 = envelope(plan2, spec, template, config, provider.name, trace.trace_id)
        issues2 = [parse_issue2] if parse_issue2 else _run_validation(plan2, spec, template)

        if errors(issues2):
            trace.latency_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                "invalid_output",
                issues=issues2,
                trace=trace,
                rejected_output=raw2,
            )
        plan, issues = plan2, issues2

    trace.latency_ms = int((time.time() - started) * 1000)
    return GenerationResult("ok", plan=plan, issues=issues, trace=trace)


def summary(result: GenerationResult) -> str:
    """给 CLI 和界面用的一行摘要。"""
    t = result.trace
    head = {
        "ok": "生成成功",
        "needs_clarification": "规格信息不足",
        "invalid_output": "输出不合法",
        "failed": "调用失败",
    }[result.status]
    parts = [head, f"模式={t.mode}", f"尝试={t.attempts} 次", f"{t.latency_ms} ms"]
    if result.plan:
        parts.append(f"任务={len(result.plan.get(F.out(F.PLAN, 'tasks'), []))} 个")
    if result.gaps:
        parts.append(f"缺口={len(result.gaps)} 项")
    if result.issues:
        parts.append(f"问题={len(result.issues)} 项")
    return "｜".join(parts)
