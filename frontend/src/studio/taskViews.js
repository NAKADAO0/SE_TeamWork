import { escapeHtml as h } from "../shared/dom.js";
import { dependencyIds } from "../shared/graph.js";

export const roleName = role => ({ backend: "服务端", frontend: "界面", qa: "测试与验证" })[role] ?? role;
const states = { waiting: "待执行", running: "执行中", paused: "已暂停", failed: "失败", done: "已完成" };
export function taskState(task, run) {
  if (Object.hasOwn(run?.results ?? {}, task.id)) return "done";
  return run?.execution?.batches?.[run.cursor]?.includes(task.id) && ["running", "paused", "failed"].includes(run.status) ? run.status : "waiting";
}
export function taskDetail(task, tasks, run) {
  const names = new Map(tasks.map(t => [t.id, t.title]));
  const refs = ids => ids.length ? ids.map(id => `<button type="button" data-task="${h(id)}">${h(names.get(id) ?? id)}</button>`).join("") : '<p class="muted">无</p>';
  const acceptance = [task.acceptance ?? []].flat();
  return `<span class="task-state">${h(states[taskState(task, run)])} · ${h(roleName(task.role))}</span><h3>${h(task.title)}</h3><small>${h(task.id)}</small><p>${h(task.description)}</p><h4>验收条件</h4><ul>${acceptance.map(a => `<li>${h(a)}</li>`).join("")}</ul><h4>前置任务</h4><div class="task-refs">${refs(dependencyIds(task))}</div><h4>后续任务</h4><div class="task-refs">${refs(tasks.filter(t => dependencyIds(t).includes(task.id)).map(t => t.id))}</div><details><summary>输入与产物</summary><pre>${h(JSON.stringify({ inputs: task.inputs, outputs: task.outputs }, null, 2))}</pre></details>${run?.results?.[task.id] ? `<h4>执行产物</h4><p>${h(run.results[task.id].summary)}</p><pre>${h(run.results[task.id].artifact)}</pre>` : ""}`;
}
