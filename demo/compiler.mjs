export function compile(tasks, capacity) {
  if (!Number.isInteger(capacity) || capacity < 1) throw Error("并发数必须为正整数");
  const pending = new Map(tasks.map(t => [t.id,t]));
  if (pending.size !== tasks.length) throw Error("任务编号重复");
  for (const t of tasks) for (const dep of t.depends_on)
    if (!pending.has(dep)) throw Error("缺少前置任务 " + dep);
  const done = new Set(), batches = [];
  while (pending.size) {
    const ready = [...pending.values()].filter(t => t.depends_on.every(x => done.has(x)))
      .map(t => t.id).sort().slice(0,capacity);
    if (!ready.length) throw Error("任务存在依赖环");
    batches.push(ready);
    for (const id of ready) { done.add(id); pending.delete(id); }
  }
  return batches;
}
