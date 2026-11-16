/**
 * 静态批次计算的临时替身（用于前端联调与演示）。
 *
 * 正式实现由成员 4 的 backend/app/orchestration 提供；本函数只保证：
 * 1. 同一输入产生同一输出（priority 升序、task_id 升序）；
 * 2. 每批不超过全局并发与角色容量；
 * 3. 无法安排时返回阻塞原因而不是抛错。
 */
import { dependencyIds, edgeList, topologicalLevels } from "./graph.js";

const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

export function schedule(tasks, config) {
  const maxConcurrency = Number(config?.max_concurrency);
  if (!Number.isInteger(maxConcurrency) || maxConcurrency < 1) {
    throw new Error("全局并发必须为正整数");
  }
  const capacity = config?.role_capacity ?? {};

  const pending = new Map();
  for (const task of tasks) {
    if (pending.has(task.id)) throw new Error(`任务编号重复：${task.id}`);
    pending.set(task.id, task);
  }
  for (const task of tasks) {
    for (const dep of dependencyIds(task)) {
      if (!pending.has(dep)) throw new Error(`缺少前置任务：${dep}`);
    }
  }

  const done = new Set();
  const batches = [];
  while (pending.size) {
    const ready = [...pending.values()]
      .filter((task) => dependencyIds(task).every((dep) => done.has(dep)))
      .sort((a, b) => (a.priority ?? 0) - (b.priority ?? 0) || compare(String(a.id), String(b.id)));

    const batch = { index: batches.length, tasks: [], bindings: {}, waiting: [] };
    const slotUsed = {};
    let globalUsed = 0;

    for (const task of ready) {
      const roleCapacity = Number(capacity[task.role] ?? 0);
      if (globalUsed >= maxConcurrency) {
        batch.waiting.push({ task_id: task.id, reason: "全局并发已满" });
        continue;
      }
      if (roleCapacity < 1) {
        batch.waiting.push({ task_id: task.id, reason: `角色 ${task.role} 未配置容量` });
        continue;
      }
      if ((slotUsed[task.role] ?? 0) >= roleCapacity) {
        batch.waiting.push({ task_id: task.id, reason: `${task.role} 容量已满` });
        continue;
      }
      slotUsed[task.role] = (slotUsed[task.role] ?? 0) + 1;
      globalUsed += 1;
      batch.tasks.push(task.id);
      (batch.bindings[task.role] ??= []).push({ task_id: task.id, slot: slotUsed[task.role] });
    }

    if (!batch.tasks.length) {
      const blocked = [...pending.values()].map((task) => {
        const missing = dependencyIds(task).filter((dep) => !done.has(dep));
        return {
          task_id: task.id,
          reason: missing.length ? `等待前置 ${missing.join("、")}` : "资源不足，无法装入任何任务",
        };
      });
      return {
        compiled: false,
        batches,
        blocked,
        nodes: tasks.map((task) => task.id),
        edges: edgeList(tasks),
        topological_levels: topologicalLevels(tasks),
      };
    }

    batches.push(batch);
    for (const id of batch.tasks) {
      done.add(id);
      pending.delete(id);
    }
  }

  return {
    compiled: true,
    batches,
    blocked: [],
    nodes: tasks.map((task) => task.id),
    edges: edgeList(tasks),
    topological_levels: topologicalLevels(tasks),
  };
}
