/**
 * P1 · 画布连线编辑的纯逻辑（成员 1）。
 *
 * 语义仍然是 TaskPlan.depends_on：连线只是另一种录入入口，不产生第二套边。
 * 提交前做一次前端环检测，服务端校验器仍是权威。
 */
import { findPath } from "../../shared/graph.js";

export function canLink(tasks, from, to) {
  const source = tasks.find((task) => task.id === from);
  const target = tasks.find((task) => task.id === to);
  if (!source || !target) {
    return { ok: false, code: "UNKNOWN_TASK", message: "节点不存在" };
  }
  if (from === to) {
    return { ok: false, code: "SELF_DEPENDENCY", message: "任务不能依赖自己" };
  }
  const duplicate = (target.depends_on ?? []).some(
    (dep) => (typeof dep === "string" ? dep : dep?.task_id) === from,
  );
  if (duplicate) {
    return { ok: false, code: "DUPLICATE_EDGE", message: `依赖 ${from} → ${to} 已存在` };
  }
  const path = findPath(tasks, to, from);
  if (path) {
    return {
      ok: false,
      code: "DEPENDENCY_CYCLE",
      message: `该连线会形成环：${[from, ...path].join(" → ")}`,
      path: [from, ...path],
    };
  }
  return { ok: true, code: "OK", message: `将添加依赖 ${from} → ${to}` };
}

export function linkTasks(tasks, from, to) {
  const verdict = canLink(tasks, from, to);
  if (!verdict.ok) return { ok: false, ...verdict, tasks };
  const next = tasks.map((task) =>
    task.id === to
      ? { ...task, depends_on: [...(task.depends_on ?? []), { task_id: from, reason: "界面连线添加的依赖" }] }
      : task,
  );
  return { ok: true, ...verdict, tasks: next };
}

export function unlinkTasks(tasks, from, to) {
  const next = tasks.map((task) =>
    task.id === to
      ? {
          ...task,
          depends_on: (task.depends_on ?? []).filter(
            (dep) => (typeof dep === "string" ? dep : dep?.task_id) !== from,
          ),
        }
      : task,
  );
  return next;
}
