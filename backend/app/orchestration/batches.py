"""确定性排批：在拓扑层之外加入资源维度（全局并发 + 角色容量 + 角色槽位）。

算法对应设计文档 §5.3：

1. 候选集合 = 前置任务全部位于“完成推演集合”的任务；
2. 候选按 ``config.ordering`` 排序，``task_id`` 始终作为最后兜底键；
3. 按全局并发与角色容量依次装入当前批次，同角色使用编号最小的空闲槽；
4. 本批为空则报告阻塞并停止；每批记录其余候选的等待原因（依赖等待 / 资源等待分开）。

“完成推演集合”只用于静态计算，不改变任何运行状态；同批任务视为可同时开展，
下一批等待上一批结束。该安排保守、可复现，不承诺最短工期。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..contracts.models import Binding
from .errors import (
    BLOCKED,
    CONFIG_INVALID,
    ROLE_CAPACITY_MISSING,
    OrchestrationError,
)
from .graph import ORDERING_KEYS, TaskGraph

#: 等待原因分类：依赖等待与资源等待必须分开显示（设计文档 §5.4）
WAIT_DEPENDENCY = "WAIT_DEPENDENCY"
WAIT_GLOBAL_CAPACITY = "WAIT_GLOBAL_CAPACITY"
WAIT_ROLE_CAPACITY = "WAIT_ROLE_CAPACITY"


@dataclass(frozen=True)
class RunConfig:
    """本次编排使用的运行配置（来自 ``TaskPlan.config``）。"""

    max_concurrency: int
    role_capacity: Mapping[str, int]
    ordering: tuple[str, ...]

    @classmethod
    def parse(cls, raw: Any) -> "RunConfig":
        """校验并规范化配置；非法配置抛 ``ORCH_CONFIG_INVALID``。"""
        if not isinstance(raw, Mapping):
            raise OrchestrationError(
                CONFIG_INVALID, "任务计划缺少 config 对象", field_path="config"
            )
        max_concurrency = raw.get("max_concurrency")
        if (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise OrchestrationError(
                CONFIG_INVALID,
                "config.max_concurrency 必须是正整数",
                field_path="config.max_concurrency",
                details={"value": max_concurrency},
            )

        capacities = raw.get("role_capacity")
        if not isinstance(capacities, Mapping) or not capacities:
            raise OrchestrationError(
                CONFIG_INVALID,
                "config.role_capacity 必须是非空对象（角色 → 正整数容量）",
                field_path="config.role_capacity",
            )
        normalized: dict[str, int] = {}
        for role, capacity in capacities.items():
            if not isinstance(role, str) or not role:
                raise OrchestrationError(
                    CONFIG_INVALID,
                    "config.role_capacity 的角色名必须是非空字符串",
                    field_path="config.role_capacity",
                    details={"role": role},
                )
            if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
                raise OrchestrationError(
                    CONFIG_INVALID,
                    f"角色 {role} 的容量必须是正整数",
                    field_path=f"config.role_capacity.{role}",
                    details={"role": role, "value": capacity},
                )
            normalized[role] = capacity

        ordering_raw = raw.get("ordering", ["priority", "task_id"])
        if not isinstance(ordering_raw, Sequence) or isinstance(ordering_raw, (str, bytes)):
            raise OrchestrationError(
                CONFIG_INVALID,
                "config.ordering 必须是排序键数组",
                field_path="config.ordering",
            )
        ordering: list[str] = []
        for index, key in enumerate(ordering_raw):
            if key not in ORDERING_KEYS:
                raise OrchestrationError(
                    CONFIG_INVALID,
                    f"config.ordering 含未知排序键 {key!r}；只允许 priority、task_id",
                    field_path=f"config.ordering[{index}]",
                    details={"key": key},
                )
            if key not in ordering:
                ordering.append(key)
        if not ordering:
            ordering = ["priority", "task_id"]
        if "task_id" not in ordering:
            # 同优先级任务必须仍有稳定顺序，否则无法满足 V07 的可复现要求。
            ordering.append("task_id")
        return cls(
            max_concurrency=max_concurrency,
            role_capacity=normalized,
            ordering=tuple(ordering),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_concurrency": self.max_concurrency,
            "role_capacity": dict(sorted(self.role_capacity.items())),
            "ordering": list(self.ordering),
        }


@dataclass
class BatchResult:
    """一批任务及其槽位、容量占用与等待原因。"""

    index: int
    tasks: list[str]
    bindings: list[Binding]
    global_used: int
    role_used: dict[str, int]
    waiting: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ScheduleResult:
    """排批结果：批次、绑定与逐批解释。"""

    batches: list[list[str]]
    bindings: list[Binding]
    details: list[BatchResult]
    config: RunConfig

    def explanation(
        self,
        graph: TaskGraph,
        *,
        plan_ref: Mapping[str, Any],
        execution_plan_id: str,
        schema_version: str = "1.0",
    ) -> dict[str, Any]:
        """把逐批解释整理成可保存、可导出的独立文档（不属于 ExecutionPlan 契约）。"""
        roles = sorted(self.config.role_capacity)
        batches: list[dict[str, Any]] = []
        waiting_total = 0
        for detail in self.details:
            waiting_total += len(detail.waiting)
            batches.append(
                {
                    "index": detail.index,
                    "tasks": list(detail.tasks),
                    "bindings": [
                        {
                            "task_id": binding.task_id,
                            "global_slot": binding.global_slot,
                            "role_slot": binding.role_slot,
                        }
                        for binding in detail.bindings
                    ],
                    "capacity": {
                        "global": {
                            "used": detail.global_used,
                            "capacity": self.config.max_concurrency,
                        },
                        "roles": {
                            role: {
                                "used": detail.role_used.get(role, 0),
                                "capacity": self.config.role_capacity[role],
                            }
                            for role in roles
                        },
                    },
                    "waiting": detail.waiting,
                }
            )
        return {
            "schema_version": schema_version,
            "execution_plan_id": execution_plan_id,
            "plan_ref": {
                "plan_id": plan_ref["plan_id"],
                "revision": plan_ref["revision"],
            },
            "config": self.config.as_dict(),
            "blocked": False,
            "summary": {
                "tasks": len(graph.task_ids),
                "batches": len(self.batches),
                "largest_batch": max((len(batch) for batch in self.batches), default=0),
                "wait_reasons": waiting_total,
                "topological_levels": len(graph.levels),
            },
            "topological_levels": [list(level) for level in graph.levels],
            "batches": batches,
        }


def _role_message(batch: Sequence[str], graph: TaskGraph, role: str, capacity: int) -> str:
    scheduled = [task_id for task_id in batch if graph.roles[task_id] == role]
    return f"角色 {role} 容量已满（{len(scheduled)}/{capacity}），本批已排入 {'、'.join(scheduled)}"


def _global_message(batch: Sequence[str], capacity: int) -> str:
    return f"全局并发已满（{len(batch)}/{capacity}），本批已排入 {'、'.join(batch)}"


def _dependency_message(
    graph: TaskGraph, task_id: str, unmet: Sequence[str], batch: Sequence[str]
) -> str:
    """依赖等待的说明；前置恰好在本批时说明下一批即可就绪，避免误读成阻塞。"""
    in_batch = [dependency_id for dependency_id in unmet if dependency_id in batch]
    if len(in_batch) == len(unmet):
        return f"本批完成后可排入（前置 {'、'.join(in_batch)} 在本批执行）"
    waiting_on = [dependency_id for dependency_id in unmet if dependency_id not in batch]
    parts: list[str] = []
    for dependency_id in waiting_on:
        reason = graph.reasons.get((dependency_id, task_id), "")
        parts.append(f"{dependency_id}（{reason}）" if reason else dependency_id)
    message = f"等待前置任务 {'、'.join(parts)}"
    if in_batch:
        message += f"；另有 {'、'.join(in_batch)} 在本批执行"
    return message


def schedule(graph: TaskGraph, config: RunConfig) -> ScheduleResult:
    """按依赖与资源容量计算批次、绑定与等待原因。"""
    capacity = config.max_concurrency
    roles = config.role_capacity
    for task_id in graph.task_ids:
        if graph.roles[task_id] not in roles:
            raise OrchestrationError(
                ROLE_CAPACITY_MISSING,
                f"任务 {task_id} 使用了角色 {graph.roles[task_id]!r}，"
                "但 config.role_capacity 未配置该角色容量",
                field_path="config.role_capacity",
                details={"task_id": task_id, "role": graph.roles[task_id]},
            )

    sorter = graph.sort_key(config.ordering)
    done: set[str] = set()
    remaining: set[str] = set(graph.task_ids)
    batches: list[list[str]] = []
    bindings: list[Binding] = []
    details: list[BatchResult] = []

    while remaining:
        ready = [
            task_id
            for task_id in graph.task_ids
            if task_id in remaining and all(dep in done for dep in graph.depends[task_id])
        ]
        ready.sort(key=sorter)

        batch: list[str] = []
        batch_bindings: list[Binding] = []
        role_used: dict[str, int] = {}
        capacity_waits: dict[str, dict[str, Any]] = {}

        for task_id in ready:
            role = graph.roles[task_id]
            if len(batch) >= capacity:
                capacity_waits[task_id] = {
                    "task_id": task_id,
                    "code": WAIT_GLOBAL_CAPACITY,
                    "role": role,
                    "message": _global_message(batch, capacity),
                    "capacity": capacity,
                    "in_use": len(batch),
                }
                continue
            limit = roles[role]
            if role_used.get(role, 0) >= limit:
                capacity_waits[task_id] = {
                    "task_id": task_id,
                    "code": WAIT_ROLE_CAPACITY,
                    "role": role,
                    "message": _role_message(batch, graph, role, limit),
                    "capacity": limit,
                    "in_use": role_used.get(role, 0),
                }
                continue
            role_used[role] = role_used.get(role, 0) + 1
            batch.append(task_id)
            batch_bindings.append(
                Binding(
                    task_id=task_id,
                    global_slot=len(batch),
                    role_slot=role_used[role],
                )
            )

        if not batch:
            raise OrchestrationError(
                BLOCKED,
                "没有任务可以安排：所有候选都被资源容量或依赖阻塞",
                field_path="batches",
                details={
                    "unscheduled": [task_id for task_id in graph.task_ids if task_id in remaining],
                    "ready": list(ready),
                    "max_concurrency": capacity,
                    "role_capacity": dict(sorted(roles.items())),
                    "reasons": [capacity_waits[task_id]["message"] for task_id in sorted(capacity_waits)],
                },
            )

        waiting: list[dict[str, Any]] = []
        for task_id in graph.task_ids:
            if task_id in remaining and task_id not in batch:
                if task_id in capacity_waits:
                    waiting.append(capacity_waits[task_id])
                    continue
                unmet = [dep for dep in graph.depends[task_id] if dep not in done]
                waiting.append(
                    {
                        "task_id": task_id,
                        "code": WAIT_DEPENDENCY,
                        "role": graph.roles[task_id],
                        "message": _dependency_message(graph, task_id, unmet, batch),
                        "blocking_task_ids": list(unmet),
                    }
                )

        index = len(batches)
        batches.append(batch)
        bindings.extend(batch_bindings)
        details.append(
            BatchResult(
                index=index,
                tasks=batch,
                bindings=batch_bindings,
                global_used=len(batch),
                role_used=dict(role_used),
                waiting=waiting,
            )
        )
        for task_id in batch:
            done.add(task_id)
            remaining.discard(task_id)

    return ScheduleResult(
        batches=batches,
        bindings=bindings,
        details=details,
        config=config,
    )


__all__ = [
    "ORDERING_KEYS",
    "WAIT_DEPENDENCY",
    "WAIT_GLOBAL_CAPACITY",
    "WAIT_ROLE_CAPACITY",
    "BatchResult",
    "RunConfig",
    "ScheduleResult",
    "schedule",
]
