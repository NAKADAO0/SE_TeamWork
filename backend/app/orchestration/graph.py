"""任务计划 → 可计算的任务图（节点、依赖边、拓扑层）。

与成员 3 契约（``backend/app/contracts/models.py``）对齐的约定：

- **边方向**：``from_task`` 是前置任务，``to_task`` 是依赖它的当前任务，
  由 ``depends_on`` 归一化得到，不新增无法追溯的任务 ID；
- **节点顺序**：沿用任务计划中的声明顺序，节点因此稳定、可追溯到计划；
- **拓扑层**：最长路径分层，``level(t) = 1 + max(level(前置))``，无前置为第 0 层，
  只表达依赖先后，不表达资源占用；层内按 ``priority`` 升序、``task_id`` 升序排列，
  与批次内的候选顺序一致，因此改任务声明顺序不会改变分层结果；
- **依赖理由**：``depends_on[i].reason`` 原样保留，供“等待原因”引用业务依据；
- **环**：环是校验器（``DEPENDENCY_CYCLE``）的职责；这里只做防御式检测，
  以免排批循环无法结束，并按同样的“当前 → 前置”方向给出闭合路径。

本模块只读取 ``id / role / stage / priority / depends_on``；任务的其他字段留在
任务计划里，不参与计算，也不在此处复制或改写。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .errors import (
    DEPENDENCY_CYCLE,
    DEPENDENCY_SELF,
    DEPENDENCY_UNKNOWN,
    PLAN_INVALID,
    TASK_ID_DUPLICATE,
    OrchestrationError,
)


#: 允许的排序键（与契约 ``ORDERING_KEYS`` 一致）
ORDERING_KEYS = ("priority", "task_id")


def _sort_key(priorities: Mapping[str, int], ordering: Iterable[str]):
    keys = [key for key in ordering if key != "task_id"]

    def key(task_id: str) -> tuple[Any, ...]:
        parts: list[Any] = []
        for name in keys:
            if name == "priority":
                parts.append(priorities[task_id])
        parts.append(task_id)
        return tuple(parts)

    return key


@dataclass(frozen=True)
class TaskGraph:
    """归一化后的任务图；所有序列都保持稳定顺序。"""

    task_ids: tuple[str, ...]
    roles: Mapping[str, str]
    stages: Mapping[str, str]
    priorities: Mapping[str, int]
    #: 任务 → 前置任务（已去重、保持声明顺序）
    depends: Mapping[str, tuple[str, ...]]
    #: (前置任务, 当前任务) → 依赖理由
    reasons: Mapping[tuple[str, str], str]
    #: 去重后的边，按“消费者任务声明顺序 + depends_on 声明顺序”排列
    edges: tuple[tuple[str, str], ...]
    #: 最长路径分层；层内按 priority、task_id 升序
    levels: tuple[tuple[str, ...], ...]

    def level_of(self) -> dict[str, int]:
        """任务 → 层号（从 0 开始）。"""
        return {task_id: index for index, level in enumerate(self.levels) for task_id in level}

    def successors(self) -> dict[str, list[str]]:
        """任务 → 后继任务（保持边顺序）。"""
        children: dict[str, list[str]] = {task_id: [] for task_id in self.task_ids}
        for from_task, to_task in self.edges:
            children[from_task].append(to_task)
        return children

    def sort_key(self, ordering: Iterable[str] = ORDERING_KEYS):
        """按配置的排序键生成排序函数；``task_id`` 永远是最后的稳定兜底键。"""
        return _sort_key(self.priorities, ordering)


def _field_error(message: str, field_path: str) -> OrchestrationError:
    return OrchestrationError(PLAN_INVALID, message, field_path=field_path)


def _dependencies(task: Mapping[str, Any], task_id: str, position: int) -> tuple[list[str], dict[str, str]]:
    """规范化 ``depends_on``：支持 ``["A"]`` 与 ``[{"task_id": "A", "reason": "…"}]``。

    重复依赖只保留第一次出现的理由；契约层已用 ``DEPENDENCY_DUPLICATE`` 报告，
    这里不重复报错，只保证计算不会因此产生重复边。
    """
    raw = task.get("depends_on")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise _field_error(
            f"任务 {task_id} 的 depends_on 应为数组", f"tasks[{position}].depends_on"
        )
    ordered: list[str] = []
    reasons: dict[str, str] = {}
    for index, dependency in enumerate(raw):
        if isinstance(dependency, str):
            dependency_id, reason = dependency, ""
        elif isinstance(dependency, Mapping):
            dependency_id = dependency.get("task_id")
            reason = dependency.get("reason") or ""
            if not isinstance(dependency_id, str) or not dependency_id:
                raise _field_error(
                    f"任务 {task_id} 的第 {index} 条依赖缺少 task_id",
                    f"tasks[{position}].depends_on[{index}].task_id",
                )
        else:
            raise _field_error(
                f"任务 {task_id} 的第 {index} 条依赖应为字符串或对象",
                f"tasks[{position}].depends_on[{index}]",
            )
        if dependency_id == task_id:
            raise OrchestrationError(
                DEPENDENCY_SELF,
                f"任务 {task_id} 不能依赖自己",
                field_path=f"tasks[{position}].depends_on[{index}].task_id",
                details={"task_id": task_id},
            )
        if dependency_id in reasons:
            continue
        reasons[dependency_id] = str(reason)
        ordered.append(dependency_id)
    return ordered, reasons


def _find_cycle(
    task_ids: Iterable[str], depends: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    """返回一条闭合环路径（方向同 ``depends_on``：当前任务 → 其前置任务）。

    与校验器 ``_find_cycle`` 的口径一致，路径末尾不重复起点，由调用方补全为
    ``A → B → A``；这样两条路径可以互相核对。
    """
    visited: set[str] = set()
    on_stack: list[str] = []
    on_set: set[str] = set()

    for root in task_ids:
        if root in visited:
            continue
        visited.add(root)
        on_stack.append(root)
        on_set.add(root)
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, index = stack[-1]
            prerequisites = depends.get(node, ())
            if index < len(prerequisites):
                stack[-1] = (node, index + 1)
                candidate = prerequisites[index]
                if candidate in on_set:
                    start = on_stack.index(candidate)
                    return tuple(on_stack[start:])
                if candidate not in visited:
                    visited.add(candidate)
                    on_stack.append(candidate)
                    on_set.add(candidate)
                    stack.append((candidate, 0))
            else:
                stack.pop()
                on_stack.pop()
                on_set.discard(node)
    return None


def format_cycle(path: Iterable[str]) -> str:
    """``("C5", "C7")`` → ``"C5 → C7 → C5"``。"""
    nodes = list(path)
    if not nodes:
        return ""
    return " → ".join(nodes + [nodes[0]])


def _levels(
    task_ids: tuple[str, ...],
    depends: Mapping[str, tuple[str, ...]],
    edges: tuple[tuple[str, str], ...],
    priorities: Mapping[str, int],
    ordering: Iterable[str],
) -> tuple[tuple[str, ...], ...]:
    """按最长路径分层；层内按 ``priority``、``task_id`` 升序，与排批口径一致。"""
    children: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for from_task, to_task in edges:
        children[from_task].append(to_task)
    indegree = {task_id: len(depends[task_id]) for task_id in task_ids}
    level = {task_id: 0 for task_id in task_ids}
    queue = deque(task_id for task_id in task_ids if indegree[task_id] == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for child in children[current]:
            if level[current] + 1 > level[child]:
                level[child] = level[current] + 1
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if visited != len(task_ids):  # pragma: no cover - _find_cycle 已先行拦截
        raise OrchestrationError(DEPENDENCY_CYCLE, "任务图存在依赖环，无法分层")
    key = _sort_key(priorities, ordering)
    grouped: dict[int, list[str]] = {}
    for task_id in task_ids:
        grouped.setdefault(level[task_id], []).append(task_id)
    return tuple(tuple(sorted(grouped[index], key=key)) for index in sorted(grouped))


def build_graph(plan: Mapping[str, Any], *, ordering: Iterable[str] = ORDERING_KEYS) -> TaskGraph:
    """把任务计划归一化为 :class:`TaskGraph`；输入不合法时抛领域错误。

    ``ordering`` 只影响层内顺序，必须与 ``config.ordering`` 一致才能让
    “层级展示”和“批次候选顺序”对齐；缺省为 ``("priority", "task_id")``。
    """
    if not isinstance(plan, Mapping):
        raise OrchestrationError(PLAN_INVALID, "任务计划必须是 JSON 对象")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise _field_error("任务计划需要非空的 tasks 数组", "tasks")

    task_ids: list[str] = []
    roles: dict[str, str] = {}
    stages: dict[str, str] = {}
    priorities: dict[str, int] = {}
    depends: dict[str, tuple[str, ...]] = {}
    reasons: dict[tuple[str, str], str] = {}

    for position, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise _field_error(f"tasks[{position}] 必须是对象", f"tasks[{position}]")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise _field_error(f"tasks[{position}] 缺少任务编号 id", f"tasks[{position}].id")
        if task_id in roles:
            raise OrchestrationError(
                TASK_ID_DUPLICATE,
                f"任务编号 {task_id} 重复",
                field_path=f"tasks[{position}].id",
                details={"task_id": task_id},
            )
        role = task.get("role")
        if not isinstance(role, str) or not role:
            raise _field_error(f"任务 {task_id} 缺少角色 role", f"tasks[{position}].role")
        stage = task.get("stage")
        if not isinstance(stage, str) or not stage:
            raise _field_error(f"任务 {task_id} 缺少阶段 stage", f"tasks[{position}].stage")
        priority = task.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise _field_error(
                f"任务 {task_id} 的 priority 必须是非负整数",
                f"tasks[{position}].priority",
            )
        dependencies, dependency_reasons = _dependencies(task, task_id, position)
        task_ids.append(task_id)
        roles[task_id] = role
        stages[task_id] = stage
        priorities[task_id] = priority
        depends[task_id] = tuple(dependencies)
        for dependency_id, reason in dependency_reasons.items():
            reasons[(dependency_id, task_id)] = reason

    known = set(task_ids)
    for position, task_id in enumerate(task_ids):
        for dependency_id in depends[task_id]:
            if dependency_id not in known:
                raise OrchestrationError(
                    DEPENDENCY_UNKNOWN,
                    f"任务 {task_id} 的前置任务 {dependency_id} 不在本计划中",
                    field_path=f"tasks[{position}].depends_on",
                    details={"task_id": task_id, "dependency_id": dependency_id},
                )

    cycle = _find_cycle(task_ids, depends)
    if cycle is not None:
        raise OrchestrationError(
            DEPENDENCY_CYCLE,
            f"任务图存在闭合环：{format_cycle(cycle)}（箭头表示依赖方向：A → B 表示 A 依赖 B）",
            field_path="tasks",
            details={"cycle": list(cycle), "cycle_text": format_cycle(cycle)},
        )

    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for task_id in task_ids:
        for dependency_id in depends[task_id]:
            pair = (dependency_id, task_id)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(pair)

    return TaskGraph(
        task_ids=tuple(task_ids),
        roles=roles,
        stages=stages,
        priorities=priorities,
        depends=depends,
        reasons=reasons,
        edges=tuple(edges),
        levels=_levels(tuple(task_ids), depends, tuple(edges), priorities, ordering),
    )


__all__ = ["ORDERING_KEYS", "TaskGraph", "build_graph", "format_cycle"]
