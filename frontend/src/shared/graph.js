/**
 * 依赖图的纯函数工具。
 * 边方向：前置任务 -> 当前任务（与设计文档 §5.2 一致）。
 */

export function dependencyIds(task) {
  return (task?.depends_on ?? [])
    .map((dep) => (typeof dep === "string" ? dep : dep?.task_id))
    .filter(Boolean);
}

export function adjacency(tasks) {
  const adj = new Map();
  for (const task of tasks) adj.set(task.id, new Set());
  for (const task of tasks) {
    for (const dep of dependencyIds(task)) {
      if (!adj.has(dep)) continue;
      adj.get(dep).add(task.id);
    }
  }
  return adj;
}

export function edgeList(tasks) {
  const edges = [];
  for (const task of tasks) {
    for (const dep of task.depends_on ?? []) {
      const id = typeof dep === "string" ? dep : dep?.task_id;
      if (!id) continue;
      edges.push({
        from: id,
        to: task.id,
        reason: typeof dep === "string" ? "" : dep.reason ?? "",
      });
    }
  }
  return edges;
}

/** 沿边方向 from -> to 找一条路径，返回 [from, ..., to]；不存在返回 null。 */
export function findPath(tasks, from, to) {
  if (from === to) return [from, to];
  const adj = adjacency(tasks);
  if (!adj.has(from)) return null;
  const previous = new Map([[from, null]]);
  const queue = [from];
  while (queue.length) {
    const current = queue.shift();
    for (const next of adj.get(current) ?? []) {
      if (previous.has(next)) continue;
      previous.set(next, current);
      if (next === to) {
        const path = [to];
        let cursor = current;
        while (cursor !== null) {
          path.unshift(cursor);
          cursor = previous.get(cursor);
        }
        return path;
      }
      queue.push(next);
    }
  }
  return null;
}

/** 找一条真实闭合环，返回 [a, b, ..., a]；无环返回 null。 */
export function findCycle(tasks) {
  const adj = adjacency(tasks);
  const state = new Map();
  const stack = [];
  const visit = (node) => {
    state.set(node, 1);
    stack.push(node);
    for (const next of adj.get(node) ?? []) {
      const seen = state.get(next) ?? 0;
      if (seen === 1) {
        const start = stack.indexOf(next);
        return [...stack.slice(start), next];
      }
      if (seen === 0) {
        const found = visit(next);
        if (found) return found;
      }
    }
    stack.pop();
    state.set(node, 2);
    return null;
  };
  for (const id of adj.keys()) {
    if ((state.get(id) ?? 0) === 0) {
      const found = visit(id);
      if (found) return found;
    }
  }
  return null;
}

/** 拓扑分层，仅表示依赖关系，不考虑资源容量。 */
export function topologicalLevels(tasks) {
  const indegree = new Map(tasks.map((task) => [task.id, dependencyIds(task).length]));
  const adj = adjacency(tasks);
  const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  let frontier = tasks.filter((task) => indegree.get(task.id) === 0).map((task) => task.id).sort(compare);
  const levels = [];
  while (frontier.length) {
    levels.push(frontier);
    const next = [];
    for (const id of frontier) {
      for (const target of adj.get(id) ?? []) {
        indegree.set(target, indegree.get(target) - 1);
        if (indegree.get(target) === 0) next.push(target);
      }
    }
    frontier = next.sort(compare);
  }
  return levels;
}
