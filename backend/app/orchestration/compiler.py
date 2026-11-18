"""确定性任务编排器（成员 4）。

严格按设计文档 §5.2–5.4 实现：
- 先计算纯依赖拓扑层（topological_levels），不因并发限制改变业务依赖边；
- 按 config.ordering（默认 priority 升序、task_id 升序）排序就绪候选；
- 受 max_concurrency 与 role_capacity 双重约束分批，分配最小可用槽位；
- 区分资源等待（全局并发 / 角色容量已满）与依赖等待（等待前置任务）；
- 无法继续安排时返回 compiled=False 与明确阻塞原因，不静默丢弃任务。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ..contracts.models import SCHEMA_VERSION, content_digest


def _dep_entries(task: dict[str, Any]) -> list[tuple[str, str]]:
    """统一读取 string 或 {task_id, reason} 形式的前置依赖。"""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in task.get("depends_on") or []:
        if isinstance(raw, str):
            dep_id, reason = raw.strip(), ""
        elif isinstance(raw, dict):
            dep_id = str(raw.get("task_id") or raw.get("task") or raw.get("id") or "").strip()
            reason = str(raw.get("reason") or raw.get("why") or "").strip()
        else:
            continue
        if dep_id and dep_id not in seen:
            seen.add(dep_id)
            entries.append((dep_id, reason))
    return entries


def _normalize_config(plan: dict[str, Any], tasks: list[dict[str, Any]], override: dict[str, Any] | None) -> dict[str, Any]:
    base_cfg = dict(plan.get("config") or {})
    if override:
        base_cfg.update({k: v for k, v in override.items() if v is not None})

    raw_max = base_cfg.get("max_concurrency", plan.get("capacity", 2))
    if isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max < 1:
        raise ValueError("全局并发 max_concurrency 必须为正整数")

    roles_in_tasks = sorted({str(t.get("role") or "backend") for t in tasks})
    raw_role_cap = base_cfg.get("role_capacity")
    if isinstance(raw_role_cap, dict) and raw_role_cap:
        role_capacity: dict[str, int] = {}
        for role, cap in raw_role_cap.items():
            if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
                raise ValueError(f"角色 {role} 的容量必须为非负整数")
            role_capacity[str(role)] = cap
    else:
        # 兼容只有 max_concurrency / capacity 的简化计划
        role_capacity = {role: raw_max for role in roles_in_tasks}

    ordering = base_cfg.get("ordering") or ["priority", "task_id"]
    if not isinstance(ordering, list) or not ordering:
        ordering = ["priority", "task_id"]

    return {
        "max_concurrency": raw_max,
        "role_capacity": role_capacity,
        "ordering": [str(item) for item in ordering],
    }


def _sort_key(task: dict[str, Any], ordering: list[str]) -> tuple:
    keys: list[Any] = []
    for item in ordering:
        if item == "priority":
            prio = task.get("priority", 0)
            keys.append(int(prio) if isinstance(prio, (int, float)) and not isinstance(prio, bool) else 0)
        elif item == "task_id":
            keys.append(str(task.get("id") or ""))
    keys.append(str(task.get("id") or ""))
    return tuple(keys)


def _compute_topological_levels(tasks: list[dict[str, Any]], deps_map: dict[str, list[str]]) -> tuple[list[list[str]], list[dict[str, str]]]:
    """返回 (拓扑层, 阻塞项)。阻塞非空表示缺前置或存在环。"""
    ids = [str(t["id"]) for t in tasks]
    id_set = set(ids)
    missing_issues: list[dict[str, str]] = []
    for tid in ids:
        missing = [dep for dep in deps_map[tid] if dep not in id_set]
        if missing:
            missing_issues.append({"task_id": tid, "reason": f"缺少前置任务 {'、'.join(missing)}"})
    if missing_issues:
        return [], missing_issues

    done: set[str] = set()
    pending = list(ids)
    levels: list[list[str]] = []
    while pending:
        wave = [tid for tid in pending if all(dep in done for dep in deps_map[tid])]
        if not wave:
            blocked = [
                {
                    "task_id": tid,
                    "reason": "任务依赖成环或等待环内前置：" + "、".join(d for d in deps_map[tid] if d not in done),
                }
                for tid in pending
            ]
            return levels, blocked
        levels.append(wave)
        done.update(wave)
        pending = [tid for tid in pending if tid not in done]
    return levels, []


def compile_plan(
    plan: dict[str, Any],
    *,
    spec: dict[str, Any] | None = None,
    template: dict[str, Any] | None = None,
    validation_report: dict[str, Any] | None = None,
    config_override: dict[str, Any] | None = None,
    execution_plan_id: str | None = None,
) -> dict[str, Any]:
    """对 TaskPlan 进行确定性排批，返回统一编排结果。"""
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("任务列表不能为空")

    by_id: dict[str, dict[str, Any]] = {}
    ordered_tasks: list[dict[str, Any]] = []
    for task in raw_tasks:
        if not isinstance(task, dict) or not str(task.get("id") or "").strip():
            raise ValueError("任务缺少有效 id")
        tid = str(task["id"]).strip()
        if tid in by_id:
            raise ValueError(f"任务编号重复：{tid}")
        by_id[tid] = task
        ordered_tasks.append(task)

    config = _normalize_config(plan, ordered_tasks, config_override)
    max_concurrency = config["max_concurrency"]
    role_capacity = config["role_capacity"]
    ordering = config["ordering"]

    deps_with_reason: dict[str, list[tuple[str, str]]] = {
        str(t["id"]): _dep_entries(t) for t in ordered_tasks
    }
    deps_map: dict[str, list[str]] = {
        tid: [dep_id for dep_id, _ in entries] for tid, entries in deps_with_reason.items()
    }

    # 检查自依赖与拓扑合法性
    for tid, dep_ids in deps_map.items():
        if tid in dep_ids:
            raise ValueError(f"任务 {tid} 不能依赖自身")

    topological_levels, topo_blocked = _compute_topological_levels(ordered_tasks, deps_map)
    edges = [
        {"from": dep_id, "to": str(t["id"]), **({"reason": reason} if reason else {})}
        for t in ordered_tasks
        for dep_id, reason in deps_with_reason[str(t["id"])]
    ]
    edge_details = [
        {"from_task": dep_id, "to_task": str(t["id"])}
        for t in ordered_tasks
        for dep_id, _ in deps_with_reason[str(t["id"])]
    ]
    node_ids = [str(t["id"]) for t in ordered_tasks]
    node_details = [
        {
            "task_id": str(t["id"]),
            "role": str(t.get("role") or "backend"),
            "stage": str(t.get("stage") or ("verify" if t.get("role") == "qa" else "implement")),
            "priority": int(t.get("priority", 0))
            if isinstance(t.get("priority", 0), int) and not isinstance(t.get("priority", 0), bool)
            else 0,
        }
        for t in ordered_tasks
    ]

    plan_id = str(plan.get("plan_id") or "PLAN-DRAFT")
    revision = int(plan.get("revision") or 1)
    exec_id = execution_plan_id or f"EXE-{plan_id}-r{revision}-{uuid4().hex[:8]}"

    if topo_blocked:
        return {
            "compiled": False,
            "execution_plan_id": exec_id,
            "plan_id": plan_id,
            "revision": revision,
            "plan_ref": {
                "plan_id": plan_id,
                "revision": revision,
                "execution_plan_id": exec_id,
                "digest": content_digest(plan),
            },
            "config": config,
            "topological_levels": topological_levels,
            "batches": [],
            "batch_details": [],
            "bindings": [],
            "nodes": node_ids,
            "node_details": node_details,
            "edges": edges,
            "edge_details": edge_details,
            "blocked": topo_blocked,
        }

    done: set[str] = set()
    pending = dict(by_id)
    batches: list[list[str]] = []
    batch_details: list[dict[str, Any]] = []
    all_bindings: list[dict[str, Any]] = []

    while pending:
        ready = sorted(
            (task for task in pending.values() if all(dep in done for dep in deps_map[str(task["id"])])),
            key=lambda task: _sort_key(task, ordering),
        )

        batch_tasks: list[str] = []
        role_bindings: dict[str, list[dict[str, Any]]] = {}
        waiting: list[dict[str, str]] = []
        role_used: dict[str, int] = {}
        global_used = 0

        for task in ready:
            tid = str(task["id"])
            role = str(task.get("role") or "backend")
            cap = role_capacity.get(role, 0)
            if cap < 1:
                waiting.append({"task_id": tid, "reason": f"角色 {role} 未配置可用容量"})
                continue
            if global_used >= max_concurrency:
                waiting.append({"task_id": tid, "reason": "全局并发已满"})
                continue
            used_for_role = role_used.get(role, 0)
            if used_for_role >= cap:
                waiting.append({"task_id": tid, "reason": f"{role} 容量已满"})
                continue

            global_used += 1
            used_for_role += 1
            role_used[role] = used_for_role
            batch_tasks.append(tid)
            role_bindings.setdefault(role, []).append({"task_id": tid, "slot": used_for_role})
            all_bindings.append(
                {
                    "task_id": tid,
                    "global_slot": global_used,
                    "role_slot": used_for_role,
                }
            )

        if not batch_tasks:
            blocked: list[dict[str, str]] = []
            for tid, task in pending.items():
                missing = [dep for dep in deps_map[tid] if dep not in done]
                role = str(task.get("role") or "backend")
                cap = role_capacity.get(role, 0)
                if missing:
                    reason = f"等待前置 {'、'.join(missing)}"
                elif cap < 1:
                    reason = f"角色 {role} 容量为 {cap}，无法调度"
                else:
                    reason = "资源容量不足，无法安排任何任务"
                blocked.append({"task_id": tid, "reason": reason})
            return {
                "compiled": False,
                "execution_plan_id": exec_id,
                "plan_id": plan_id,
                "revision": revision,
                "plan_ref": {
                    "plan_id": plan_id,
                    "revision": revision,
                    "execution_plan_id": exec_id,
                    "digest": content_digest(plan),
                },
                "config": config,
                "topological_levels": topological_levels,
                "batches": batches,
                "batch_details": batch_details,
                "bindings": all_bindings,
                "nodes": node_ids,
                "node_details": node_details,
                "edges": edges,
                "edge_details": edge_details,
                "blocked": blocked,
            }

        # 补充本批直接被前置依赖阻塞的待执行任务原因，区分“资源等待”与“依赖等待”
        batch_set = set(batch_tasks)
        for tid, task in pending.items():
            if tid in batch_set or any(w["task_id"] == tid for w in waiting):
                continue
            unmet = [dep for dep in deps_map[tid] if dep not in done]
            if unmet and any(dep in batch_set for dep in unmet):
                waiting.append({"task_id": tid, "reason": f"等待前置 {'、'.join(unmet)}"})

        batch_details.append(
            {
                "index": len(batches),
                "tasks": batch_tasks,
                "bindings": role_bindings,
                "waiting": waiting,
            }
        )
        batches.append(batch_tasks)
        for tid in batch_tasks:
            done.add(tid)
            pending.pop(tid, None)

    result: dict[str, Any] = {
        "compiled": True,
        "execution_plan_id": exec_id,
        "plan_id": plan_id,
        "revision": revision,
        "plan_ref": {
            "plan_id": plan_id,
            "revision": revision,
            "execution_plan_id": exec_id,
            "digest": content_digest(plan),
        },
        "config": config,
        "topological_levels": topological_levels,
        "batches": batches,
        "batch_details": batch_details,
        "bindings": all_bindings,
        "nodes": node_ids,
        "node_details": node_details,
        "edges": edges,
        "edge_details": edge_details,
        "blocked": [],
    }

    if spec is not None or validation_report is not None:
        result["schema_execution_plan"] = to_schema_execution_plan(
            result,
            plan=plan,
            spec=spec or plan.get("spec") or {},
            template=template or {},
            validation_report=validation_report or {},
        )
    return result


def to_schema_execution_plan(
    compiled_result: dict[str, Any],
    *,
    plan: dict[str, Any],
    spec: dict[str, Any],
    template: dict[str, Any],
    validation_report: dict[str, Any],
) -> dict[str, Any]:
    """构造满足 backend/app/contracts/schema/execution_plan.schema.json 的 ExecutionPlan 文档。"""
    plan_id = str(plan.get("plan_id") or compiled_result["plan_id"])
    revision = int(plan.get("revision") or compiled_result["revision"])
    raw_exec_id = str(compiled_result["execution_plan_id"])
    if not raw_exec_id[0:1].isalpha():
        raw_exec_id = f"EXE-{raw_exec_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_plan_id": raw_exec_id,
        "plan_ref": {
            "plan_id": plan_id,
            "revision": revision,
        },
        "spec_digest": content_digest(spec),
        "template_digest": content_digest(template),
        "config_digest": content_digest(compiled_result["config"]),
        "nodes": compiled_result["node_details"],
        "edges": compiled_result["edge_details"],
        "topological_levels": compiled_result["topological_levels"],
        "batches": compiled_result["batches"],
        "bindings": compiled_result["bindings"],
        "validation_ref": {
            "plan_id": plan_id,
            "revision": revision,
            "report_digest": content_digest(validation_report) if validation_report else content_digest(plan),
        },
    }
