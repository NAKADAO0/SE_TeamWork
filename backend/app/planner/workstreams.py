"""Validate PM work packages separately from the TaskPlan interoperability schema."""
import re


def validate_workstreams(raw, tasks):
    """Return a normalized partition of the real task IDs or request a model repair."""
    ids = {task["id"] for task in tasks if isinstance(task.get("id"), str)}
    if not isinstance(raw, list) or not (2 if len(ids) >= 4 else 1) <= len(raw) <= min(8, len(ids)):
        raise ValueError("请给出 1–8 个工作包；达到四个小任务时至少分为两个工作包。")
    groups, names, assigned = [], set(), set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("每个工作包须包含 id、title、summary 和 task_ids。")
        key, title, summary, children = (item.get(k) for k in ("id", "title", "summary", "task_ids"))
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", key) or key in names:
            raise ValueError("工作包编号须唯一，只能使用字母、数字、下划线和连字符。")
        if not isinstance(title, str) or not isinstance(summary, str) or not 1 <= len(title.strip()) <= 60 or not 1 <= len(summary.strip()) <= 160:
            raise ValueError("工作包须有明确的标题和产出简介。")
        if not isinstance(children, list) or not children:
            raise ValueError("每个工作包至少包含一个小任务。")
        for tid in children:
            if not isinstance(tid, str) or tid not in ids or tid in assigned:
                raise ValueError("小任务必须真实存在，且不能在多个工作包重复出现。")
            assigned.add(tid)
        names.add(key)
        groups.append({"id": key, "title": title.strip(), "summary": summary.strip(), "task_ids": children[:]})
    if assigned != ids:
        raise ValueError("有小任务未归入工作包，请将全部小任务恰好归组一次。")
    return groups
