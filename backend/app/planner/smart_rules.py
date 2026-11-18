"""面向任意完整 DesignSpec 的结构化任务分解器。

严格依据输入的设计规格（product、requirements、modules、design_items/design_refs、
constraints）与工作流模板（stages、allowed_roles、require_verification）推导任务，
不预设具体产品名称或固定答案，并保证产出严格满足 TaskPlan Schema 与校验规范。
"""

from __future__ import annotations

import re
from typing import Any

from . import fields as F
from .models import ProviderError
from .service import envelope

_UI_KEYWORDS = ("界面", "页面", "前端", "ui", "web", "交互", "表单", "列表", "展示", "工作台", "客户端")
_SLUG_INVALID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_id(prefix: str, raw: str, used: set[str]) -> str:
    cleaned = _SLUG_INVALID_RE.sub("-", str(raw)).strip("-") or "X"
    candidate = f"{prefix}-{cleaned}"
    if not candidate[0].isalpha():
        candidate = f"T-{candidate}"
    base = candidate
    idx = 2
    while candidate in used:
        candidate = f"{base}-{idx}"
        idx += 1
    used.add(candidate)
    return candidate


def _is_ui_module(mod: dict[str, Any]) -> bool:
    mid = str(mod.get("id") or "").lower()
    resp = str(mod.get("responsibility") or "").lower()
    return any(kw in mid or kw in resp for kw in _UI_KEYWORDS)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _short_goal(goal: str, limit: int = 16) -> str:
    head = re.split(r"[，,。；;\n]", goal.strip())[0].strip()
    if len(head) <= limit:
        return head
    return head[: limit - 1].rstrip() + "…"


def _acc_list(raw: Any, fallback: str) -> list[str]:
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    elif raw is None:
        items = []
    else:
        items = [part.strip() for part in re.split(r"[；;\n]+", str(raw)) if part.strip()]
    return items or [fallback]


def smart_decompose(spec: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    """根据完整/半结构化 DesignSpec 生成符合 TaskPlan 契约的任务列表与假设。"""
    template = template or {}
    stages = [str(s) for s in (template.get("stages") or ["implement", "verify", "integrate"])]
    allowed_roles = [str(r) for r in (template.get("allowed_roles") or ["backend", "frontend", "qa"])]
    require_verification = bool(template.get("require_verification", True))

    be_role = "backend" if "backend" in allowed_roles else allowed_roles[0]
    fe_role = "frontend" if "frontend" in allowed_roles else None
    qa_role = "qa" if "qa" in allowed_roles else allowed_roles[-1]

    design_stage = "design" if "design" in stages else stages[0]
    impl_stage = "implement" if "implement" in stages else stages[0]
    verify_stage = "verify" if "verify" in stages else stages[-1]
    integrate_stage = "integrate" if "integrate" in stages else verify_stage

    raw_product = spec.get("product")
    product = raw_product.get("name", "") if isinstance(raw_product, dict) else str(raw_product or "产品")
    reqs = [r for r in (F.pick(spec, F.SPEC, "requirements") or []) if isinstance(r, dict)]
    modules = [m for m in (F.pick(spec, F.SPEC, "modules") or []) if isinstance(m, dict)]
    design_items = [d for d in (F.pick(spec, F.SPEC, "design_refs") or []) if isinstance(d, dict)]

    all_req_ids = [str(r.get("id")) for r in reqs if r.get("id")]
    design_by_id = {str(d["id"]): d for d in design_items if d.get("id")}
    has_ui_design = any(str(d.get("kind") or d.get("type") or "") == "page" for d in design_items)
    has_ui_module = any(_is_ui_module(m) for m in modules)

    used_task_ids: set[str] = set()
    used_output_ids: set[str] = set()
    tasks: list[dict[str, Any]] = []

    def next_out(raw: str) -> str:
        return _safe_id("O", raw, used_output_ids)

    # 1. 若存在多模块/设计条目或前后端协作，先建立契约与数据模型任务作为共同上游
    contract_task_id: str | None = None
    contract_out_id: str | None = None
    contract_design_ids = [
        str(d["id"])
        for d in design_items
        if str(d.get("kind") or d.get("type") or "") in ("interface", "entity", "rule", "consistency")
    ]
    if (modules or design_items) and (len(reqs) > 1 or (fe_role and (has_ui_design or has_ui_module)) or contract_design_ids):
        contract_task_id = _safe_id("T", "CONTRACT", used_task_ids)
        contract_out_id = next_out("CONTRACT")
        contract_mods = [str(m["id"]) for m in modules if m.get("id") and not _is_ui_module(m)]
        if not contract_mods:
            contract_mods = [str(m["id"]) for m in modules if m.get("id")]
        contract_refs = contract_design_ids or list(design_by_id.keys())
        tasks.append({
            "id": contract_task_id,
            "title": f"定义 {product} 接口契约与数据模型",
            "description": "统一接口字段、错误码与数据一致性规则，供前后端并行实现与验证对照。",
            "requirement_ids": all_req_ids or ["R1"],
            "module_ids": contract_mods,
            "design_refs": contract_refs,
            "kind": "support",
            "stage": design_stage,
            "role": be_role,
            "inputs": [{"source": "design", "ref": ref, "type": "design_contract"} for ref in contract_refs],
            "outputs": [
                {
                    "id": contract_out_id,
                    "type": "design_contract",
                    "description": f"{product} 接口契约与数据规则基线",
                }
            ],
            "acceptance": [
                "全部接口入参、出参与错误码定义写入契约",
                "数据实体与一致性约束可供下游实现直接消费",
            ],
            "depends_on": [],
            "priority": 0,
            "estimated_effort": {"value": 4, "unit": "人时", "basis": "梳理规格接口与数据实体契约"},
        })

    verify_tasks: list[tuple[str, str]] = []

    for req in reqs:
        rid = str(req.get("id") or "").strip()
        goal = str(req.get("goal") or "").strip()
        short_goal = _short_goal(goal)
        acc = _acc_list(req.get("acceptance"), f"{goal} 按规格验收通过")

        matched_mods = [
            m for m in modules if rid in [str(x) for x in _as_list(m.get("requirement_ids"))]
        ]
        be_mods = [str(m["id"]) for m in matched_mods if m.get("id") and not _is_ui_module(m)]
        fe_mods = [str(m["id"]) for m in matched_mods if m.get("id") and _is_ui_module(m)]
        if not be_mods and matched_mods and not fe_mods:
            be_mods = [str(m["id"]) for m in matched_mods if m.get("id")]

        matched_designs = [
            d for d in design_items
            if rid in [str(x) for x in _as_list(d.get("requirement_ids"))]
        ]
        be_design_refs = [
            str(d["id"])
            for d in matched_designs
            if str(d.get("kind") or d.get("type") or "") != "page"
        ]
        fe_design_refs = [
            str(d["id"])
            for d in matched_designs
            if str(d.get("kind") or d.get("type") or "") == "page"
        ]
        if not be_design_refs and contract_design_ids:
            # 尝试按关键词匹配未标明 requirement_ids 的设计条目
            be_design_refs = [
                str(d["id"]) for d in design_items
                if str(d.get("kind") or d.get("type") or "") != "page"
                and (rid in str(d.get("description") or "") or not d.get("requirement_ids"))
            ]
        if not be_design_refs and design_items and not matched_designs:
            be_design_refs = [
                str(d["id"]) for d in design_items
                if str(d.get("kind") or d.get("type") or "") != "page"
            ][:2]

        # 2a. 后端实现任务
        be_task_id = _safe_id("T", f"{rid}-API", used_task_ids)
        be_out_id = next_out(f"{rid}-API")
        be_inputs: list[dict[str, str]] = [
            {"source": "design", "ref": ref, "type": "design_contract"} for ref in be_design_refs
        ]
        be_deps: list[dict[str, str]] = []
        if contract_task_id and contract_out_id:
            be_inputs.append(
                {"source": "task_output", "ref": f"{contract_task_id}/{contract_out_id}", "type": "design_contract"}
            )
            be_deps.append({"task_id": contract_task_id, "reason": "依据已冻结的接口契约与数据规则实现"})

        tasks.append({
            "id": be_task_id,
            "title": f"实现{short_goal}接口",
            "description": f"完成需求 {rid}「{goal}」对应的业务接口、数据持久化与异常处理。",
            "requirement_ids": [rid],
            "module_ids": be_mods or ([str(m["id"]) for m in matched_mods if m.get("id")]),
            "design_refs": be_design_refs,
            "kind": "implementation",
            "stage": impl_stage,
            "role": be_role,
            "inputs": be_inputs,
            "outputs": [
                {"id": be_out_id, "type": "implementation", "description": f"{goal} 的服务端接口实现"}
            ],
            "acceptance": acc,
            "depends_on": be_deps,
            "priority": 1,
            "estimated_effort": {"value": 8, "unit": "人时", "basis": f"依据需求 {rid} 业务规则与接口复杂度"},
        })

        impl_producers: list[tuple[str, str, str]] = [(be_task_id, be_out_id, "需要可调用的服务端接口实现")]

        # 2b. 前端界面任务（若规格含页面设计条目或前端模块且允许 frontend 角色）
        need_fe = fe_role is not None and (bool(fe_mods) or bool(fe_design_refs) or has_ui_design)
        if need_fe:
            if not fe_design_refs:
                fe_design_refs = [
                    str(d["id"]) for d in design_items
                    if str(d.get("kind") or d.get("type") or "") == "page"
                ] or be_design_refs[:2]
            fe_task_id = _safe_id("T", f"{rid}-UI", used_task_ids)
            fe_out_id = next_out(f"{rid}-UI")
            fe_inputs: list[dict[str, str]] = [
                {"source": "design", "ref": ref, "type": "design_contract"} for ref in fe_design_refs
            ]
            fe_deps: list[dict[str, str]] = []
            if contract_task_id and contract_out_id:
                fe_inputs.append(
                    {"source": "task_output", "ref": f"{contract_task_id}/{contract_out_id}", "type": "design_contract"}
                )
                fe_deps.append({"task_id": contract_task_id, "reason": "按契约字段并行开发前端交互与状态"})
            else:
                fe_inputs.append(
                    {"source": "task_output", "ref": f"{be_task_id}/{be_out_id}", "type": "implementation"}
                )
                fe_deps.append({"task_id": be_task_id, "reason": "对接服务端接口实现"})

            tasks.append({
                "id": fe_task_id,
                "title": f"开发{short_goal}界面",
                "description": f"实现需求 {rid}「{goal}」对应的页面视图、表单校验、加载与异常提示。",
                "requirement_ids": [rid],
                "module_ids": fe_mods or ([str(m["id"]) for m in modules if _is_ui_module(m) and m.get("id")]),
                "design_refs": fe_design_refs,
                "kind": "implementation",
                "stage": impl_stage,
                "role": fe_role,
                "inputs": fe_inputs,
                "outputs": [
                    {"id": fe_out_id, "type": "implementation", "description": f"{goal} 的前端页面实现"}
                ],
                "acceptance": [f"{a}（界面状态与错误提示可操作）" for a in acc[:1]],
                "depends_on": fe_deps,
                "priority": 1,
                "estimated_effort": {"value": 6, "unit": "人时", "basis": f"依据需求 {rid} 交互视图与状态处理"},
            })
            impl_producers.append((fe_task_id, fe_out_id, "需要可操作的前端界面实现"))

        # 2c. 需求验证任务
        if require_verification:
            ver_task_id = _safe_id("T", f"{rid}-VERIFY", used_task_ids)
            ver_out_id = next_out(f"{rid}-VERIFY")
            ver_refs = list(dict.fromkeys(be_design_refs + fe_design_refs))
            ver_inputs = [
                {"source": "design", "ref": ref, "type": "design_contract"} for ref in ver_refs
            ] + [
                {"source": "task_output", "ref": f"{tid}/{oid}", "type": "implementation"}
                for tid, oid, _ in impl_producers
            ]
            ver_deps = [{"task_id": tid, "reason": reason} for tid, _, reason in impl_producers]
            tasks.append({
                "id": ver_task_id,
                "title": f"验证{short_goal}",
                "description": f"对照需求 {rid}「{goal}」的验收条件验证正常路径、权限与失败处理。",
                "requirement_ids": [rid],
                "module_ids": [str(m["id"]) for m in matched_mods if m.get("id")],
                "design_refs": ver_refs,
                "kind": "verification",
                "stage": verify_stage,
                "role": qa_role,
                "inputs": ver_inputs,
                "outputs": [
                    {"id": ver_out_id, "type": "verification_report", "description": f"需求 {rid} 验收测试报告"}
                ],
                "acceptance": acc,
                "depends_on": ver_deps,
                "priority": 2,
                "estimated_effort": {"value": 4, "unit": "人时", "basis": f"覆盖需求 {rid} 正常与边界测试"},
            })
            verify_tasks.append((ver_task_id, ver_out_id))

    # 3. 多需求端到端集成验收任务
    if len(verify_tasks) >= 2 and "integrate" in stages:
        e2e_id = _safe_id("T", "E2E", used_task_ids)
        e2e_out_id = next_out("E2E")
        e2e_refs = [str(d["id"]) for d in design_items if d.get("id")][:4]
        tasks.append({
            "id": e2e_id,
            "title": f"端到端联调与 {product} 整体验收",
            "description": f"串联 {product} 全部核心业务链路，验证跨模块数据一致性与交付质量。",
            "requirement_ids": all_req_ids or ["R1"],
            "module_ids": [str(m["id"]) for m in modules if m.get("id")],
            "design_refs": e2e_refs,
            "kind": "integration",
            "stage": integrate_stage,
            "role": qa_role,
            "inputs": [
                {"source": "task_output", "ref": f"{tid}/{oid}", "type": "verification_report"}
                for tid, oid in verify_tasks
            ],
            "outputs": [
                {"id": e2e_out_id, "type": "integration_report", "description": f"{product} 端到端集成验收报告"}
            ],
            "acceptance": [
                f"串联 {len(reqs)} 项需求的完整端到端流程全部通过并保留测试记录",
            ],
            "depends_on": [
                {"task_id": tid, "reason": "依赖各模块验证报告全部通过"} for tid, _ in verify_tasks
            ],
            "priority": 3,
            "estimated_effort": {"value": 4, "unit": "人时", "basis": "端到端联调与回归验收"},
        })

    assumptions: list[str] = []
    constraints = spec.get("constraints")
    if isinstance(constraints, list):
        for c in constraints:
            if isinstance(c, dict) and c.get("detail"):
                assumptions.append(f"遵循规格约束：{c['detail']}")
    elif isinstance(constraints, dict):
        for k, v in constraints.items():
            assumptions.append(f"遵循规格约束（{k}）：{v}")

    return {
        "tasks": tasks,
        "assumptions": assumptions,
        "clarifications": [],
    }


class StructuredRuleProvider:
    """基于设计规格与模板的结构化规则分解提供方（mode=offline_rule）。"""

    name = "offline_rule"

    def complete(self, messages: list[dict[str, Any]]) -> str:
        import json
        from .providers import _extract_payload

        payload = _extract_payload(messages)
        plan = smart_decompose(
            payload.get("design_spec") or {},
            payload.get("workflow_template") or {},
        )
        return json.dumps(plan, ensure_ascii=False)


def plan_with_provider(
    spec: dict[str, Any],
    template: dict[str, Any],
    config: dict[str, Any],
    mode: str = "offline_rule",
    custom_planner=None,
) -> dict[str, Any]:
    """统一规划调用：支持 offline_rule、live_llm、fixture_replay。"""
    from .providers import HttpProvider
    from .service import generate, spec_id_of

    if mode == "live_llm":
        if custom_planner is not None:
            tasks = custom_planner(spec)
            if not isinstance(tasks, list) or not tasks:
                raise ValueError("规划服务应返回非空任务数组")
            plan_body = envelope(
                {"tasks": tasks, "assumptions": [], "clarifications": []},
                spec,
                template,
                config,
                "live_llm",
                "custom",
            )
            return {
                "status": "succeeded",
                "plan": plan_body,
                "trace": {"mode": "live_llm", "attempts": 1},
            }
        provider = HttpProvider.from_env()
        result = generate(spec, template, config, provider)
        if result.status == "ok":
            return {
                "status": "succeeded",
                "plan": result.plan,
                "trace": vars(result.trace),
                "warnings": [vars(w) for w in result.warnings],
            }
        if result.status == "needs_clarification":
            return {
                "status": "needs_clarification",
                "gaps": [vars(g) for g in result.gaps],
                "trace": vars(result.trace),
                "error": {"code": "NEEDS_CLARIFICATION", "message": "规格存在关键缺口，请先补齐"},
            }
        if result.status == "invalid_output":
            return {
                "status": "invalid_output",
                "issues": [vars(i) for i in result.issues],
                "rejected_output": result.rejected_output,
                "trace": vars(result.trace),
                "error": {"code": "INVALID_OUTPUT", "message": "模型输出经一次自动修复后仍未通过校验"},
            }
        raise ProviderError("；".join(i.message for i in result.issues) or "模型调用失败")

    if mode == "offline_rule":
        raw_plan = smart_decompose(spec, template)
        plan_body = envelope(
            raw_plan,
            {**spec, "spec_id": spec.get("spec_id") or spec_id_of(spec)},
            template,
            config,
            "offline_rule",
            "rule",
        )
        return {
            "status": "succeeded",
            "plan": plan_body,
            "trace": {"mode": "offline_rule", "attempts": 1, "prompt_version": "rule-v2"},
        }

    raise ValueError(f"不支持的生成模式：{mode}")
