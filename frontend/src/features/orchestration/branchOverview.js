import { adjacency, dependencyIds } from "../../shared/graph.js";

export function branchOverview(tasks = [], batches = []) {
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const edges = adjacency(tasks);
  const seenFrom = new Map();
  const canReach = (from, to) => {
    if (!seenFrom.has(from)) {
      const seen = new Set([from]);
      const queue = [from];
      for (const current of queue) for (const next of edges.get(current) ?? []) {
        if (seen.has(next)) continue;
        seen.add(next);
        queue.push(next);
      }
      seenFrom.set(from, seen);
    }
    return seenFrom.get(from).has(to);
  };
  const handoffs = [];
  const joins = [];
  for (const task of tasks) {
    const deps = [...new Set(dependencyIds(task))].filter((id) => byId.has(id));
    for (const id of deps) {
      const before = byId.get(id);
      if (before.role && task.role && before.role !== task.role) handoffs.push({ from: before, to: task });
    }
    // A dependency preceding another direct input is not an independent branch.
    const inputs = deps.filter((id) =>
      !deps.some((other) => other !== id && canReach(id, other))
    ).map((id) => byId.get(id));
    if (inputs.length > 1) joins.push({ task, inputs });
  }
  const parallel = batches.map((batch, index) => ({
    number: index + 1, ids: Array.isArray(batch) ? batch : batch.tasks ?? [],
  })).filter(({ ids }) => ids.length > 1);
  return { handoffs, joins, parallel };
}
