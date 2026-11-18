"""兼容层：把编排算法接到成员 5 现有的 ``compiler(plan) -> 批次`` 注入点。

``backend/delivery.py`` 的编译端点目前使用 ``compiler(plan)`` 返回 ``[[task_id, ...], ...]``
（默认走 ``demo/compiler.js`` 的 Node 规则服务）。为了让既有接线不改动也能用上
真实算法，本模块提供两种形状：

- :func:`compile_batches` —— 直接返回批次数组，可直接作为 ``create_app(compiler=...)``；
- :func:`as_compiler` —— 返回一个 ``compiler(plan)`` 函数，便于注入并复用同一份配置。

同时接受两种计划形状：旧 Demo 的 ``{capacity, tasks:[{id, depends_on:[id]}]}`` 与
契约 TaskPlan 的 ``{config, tasks:[{id, role, stage, priority, depends_on:[{task_id}]}]}``。
领域规则与 :func:`~backend.app.orchestration.service.compile_plan` 完全一致，
只是不要求校验报告、不产出 ExecutionPlan——那是成员 5 接入完整接口后的路径。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .batches import RunConfig, schedule
from .errors import PLAN_INVALID, ROLE_CAPACITY_MISSING, OrchestrationError
from .graph import build_graph


def _tasks_of(plan: Any) -> list[Mapping[str, Any]]:
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else plan
    if not isinstance(tasks, list) or not tasks:
        raise OrchestrationError(PLAN_INVALID, "编排输入需要非空的 tasks 数组", field_path="tasks")
    return tasks


def _normalize_task(task: Any, position: int) -> dict[str, Any]:
    """补齐旧 Demo 任务缺少的 role/stage/priority，语义保持不变。

    旧任务没有阶段概念，用 ``"unspecified"`` 占位；没有优先级时取 0，
    于是候选顺序退化为按 task_id 升序（与 Demo 的 ``sort()`` 一致）。
    """
    if not isinstance(task, Mapping):
        raise OrchestrationError(
            PLAN_INVALID, f"tasks[{position}] 必须是对象", field_path=f"tasks[{position}]"
        )
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise OrchestrationError(
            PLAN_INVALID, f"tasks[{position}] 缺少任务编号 id", field_path=f"tasks[{position}].id"
        )
    priority = task.get("priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
        raise OrchestrationError(
            PLAN_INVALID,
            f"任务 {task_id} 的 priority 必须是非负整数",
            field_path=f"tasks[{position}].priority",
        )
    depends_on = task.get("depends_on")
    if depends_on is None:
        depends_on = []
    if not isinstance(depends_on, list):
        raise OrchestrationError(
            PLAN_INVALID,
            f"任务 {task_id} 的 depends_on 应为数组",
            field_path=f"tasks[{position}].depends_on",
        )
    return {
        "id": task_id,
        "role": str(task.get("role") or "default"),
        "stage": str(task.get("stage") or "unspecified"),
        "priority": priority,
        "depends_on": depends_on,
    }


def _config_of(plan: Any, config: Mapping[str, Any] | None) -> RunConfig:
    """按“显式配置 > 计划内 config > 旧 Demo 的 capacity”的优先级读取运行配置。"""
    if config is not None:
        return RunConfig.parse(config)
    if isinstance(plan, Mapping):
        if isinstance(plan.get("config"), Mapping):
            return RunConfig.parse(plan["config"])
        capacity = plan.get("capacity")
        if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity >= 1:
            roles = {
                str(task.get("role") or "default")
                for task in _tasks_of(plan)
                if isinstance(task, Mapping)
            }
            return RunConfig.parse(
                {
                    "max_concurrency": capacity,
                    # 旧 Demo 只限制全局并发，不区分角色；因此每个角色都给足全局额度。
                    "role_capacity": {role: capacity for role in sorted(roles)},
                    "ordering": ["priority", "task_id"],
                }
            )
    return RunConfig.parse(
        {"max_concurrency": 2, "role_capacity": {"default": 2}, "ordering": ["priority", "task_id"]}
    )


def _normalized_plan(plan: Any, config: Mapping[str, Any] | None) -> tuple[dict[str, Any], RunConfig]:
    tasks = [_normalize_task(task, position) for position, task in enumerate(_tasks_of(plan))]
    run_config = _config_of(plan, config)
    roles = {str(task["role"]) for task in tasks}
    missing = sorted(roles - set(run_config.role_capacity))
    if missing:
        raise OrchestrationError(
            ROLE_CAPACITY_MISSING,
            f"以下角色缺少容量配置：{'、'.join(missing)}",
            field_path="role_capacity",
            details={"roles": missing},
        )
    document: dict[str, Any] = {
        "plan_id": "PLAN-ADAPTER",
        "revision": 1,
        "config": {
            "max_concurrency": run_config.max_concurrency,
            "role_capacity": dict(run_config.role_capacity),
            "ordering": list(run_config.ordering),
        },
        "tasks": tasks,
    }
    return document, run_config


def compile_batches(
    plan: Any, *, config: Mapping[str, Any] | None = None
) -> list[list[str]]:
    """旧注入点形态：返回 ``[[task_id, ...], ...]``。

    ``plan`` 可以是 ``{tasks, capacity}``、契约 TaskPlan，或直接是任务数组；
    ``config`` 显式给出 ``max_concurrency/role_capacity/ordering`` 时优先使用。
    """
    document, run_config = _normalized_plan(plan, config)
    graph = build_graph(document, ordering=run_config.ordering)
    return [list(batch) for batch in schedule(graph, run_config).batches]


def explain_batches(
    plan: Any, *, config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """旧注入点形态但需要解释时使用：返回与 ExecutionPlan 配套的等待原因文档。"""
    document, run_config = _normalized_plan(plan, config)
    graph = build_graph(document, ordering=run_config.ordering)
    result = schedule(graph, run_config)
    return result.explanation(
        graph,
        plan_ref={"plan_id": document["plan_id"], "revision": document["revision"]},
        execution_plan_id="EXEC-ADAPTER",
    )


def as_compiler(
    config: Mapping[str, Any] | None = None,
) -> Callable[[Any], list[list[str]]]:
    """返回可直接传给 ``create_app(compiler=...)`` 的函数。"""

    def compiler(plan: Any) -> list[list[str]]:
        return compile_batches(plan, config=config)

    return compiler


def build_graph_preview(plan: Any) -> list[dict[str, Any]]:
    """供界面调试：任务图与拓扑层预览，不涉及批次。"""
    document, run_config = _normalized_plan(plan, None)
    graph = build_graph(document, ordering=run_config.ordering)
    level_of = graph.level_of()
    return [
        {
            "task_id": task_id,
            "role": graph.roles[task_id],
            "stage": graph.stages[task_id],
            "priority": graph.priorities[task_id],
            "level": level_of[task_id],
            "depends_on": list(graph.depends[task_id]),
        }
        for task_id in graph.task_ids
    ]


__all__ = [
    "as_compiler",
    "build_graph_preview",
    "compile_batches",
    "explain_batches",
]
