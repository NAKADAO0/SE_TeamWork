import { dependencyIds } from "../../shared/graph.js";

const stageNames = {
  design:"接口与设计准备", implement:"功能实现", verify:"测试与验证", integrate:"联调与验收",
};

export function makeWorkstreams(plan) {
  const tasks = plan?.tasks ?? [];
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const model = plan?.workstreams;
  let groups;
  if (Array.isArray(model) && model.length && model.every((item) =>
      item?.id && item.title && Array.isArray(item.task_ids) && item.task_ids.length)) {
    const ids = model.flatMap((group) => group.task_ids);
    if (ids.length === tasks.length && new Set(ids).size === tasks.length && ids.every((id) => byId.has(id)) &&
        new Set(model.map((group) => group.id)).size === model.length) {
      groups = model.map((group) => ({ ...group, tasks: group.task_ids.map((id) => byId.get(id)) }));
    }
  }
  const origin = groups ? "ai" : tasks.length ? "derived" : "empty";
  if (!groups) {
    const spec = plan?.normalized_spec ?? plan?.spec;
    const modules = new Map((Array.isArray(spec?.modules) ? spec.modules : []).map((module) => [module.id, module]));
    const buckets = new Map();
    for (const task of tasks) {
      const module = (task.module_ids ?? []).map((id) => modules.get(id)).find(Boolean);
      const phase = task.stage ?? task.kind ?? "";
      const key = module ? `module:${module.id}` : `stage:${phase}`;
      if (!buckets.has(key)) {
        const heading = module?.name || module?.responsibility?.split(/[：:；。]/)[0]?.slice(0, 22);
        const legacy = stageNames[phase] ?? ({ verification:"测试与验证", implementation:"功能实现", integration:"联调与验收" })[phase];
        const role = ({ backend:"服务与接口", frontend:"界面与交互", qa:"测试与验证" })[task.role];
        buckets.set(key, { title: heading || legacy || role || "开发任务", summary: module?.responsibility ?? "", tasks:[] });
      }
      buckets.get(key).tasks.push(task);
    }
    groups = [...buckets.values()].map((group, index) => ({ ...group, id:`H${index+1}`, task_ids:group.tasks.map((task)=>task.id) }));
  }
  const owner = new Map(groups.flatMap((group) => group.tasks.map((task) => [task.id, group.id])));
  const links = new Map();
  for (const task of tasks) for (const fromTask of dependencyIds(task)) {
    const from = owner.get(fromTask), to = owner.get(task.id);
    if (!from || !to || from === to) continue;
    const key = JSON.stringify([from, to]);
    if (!links.has(key)) links.set(key, { from, to, dependencies: [] });
    links.get(key).dependencies.push({ from: byId.get(fromTask), to: task });
  }
  return { origin, groups, edges:[...links.values()] };
}

export function workstreamProgress(group, run) {
  const active = new Set(run?.execution?.batches?.[run.cursor] ?? []);
  const inBatch = new Set(["running", "paused", "failed"]);
  const tasks = group.tasks.map((task) => ({
    ...task,
    status: Object.hasOwn(run?.results ?? {}, task.id) ? "done"
      : active.has(task.id) && inBatch.has(run.status) ? run.status : "waiting",
  }));
  const complete = tasks.filter((t) => t.status === "done").length;
  const status = complete === tasks.length && tasks.length ? "done"
    : tasks.some((t) => t.status === "paused" || t.status === "failed") ? "blocked"
    : tasks.some((t) => t.status === "running") || complete ? "active" : "waiting";
  return { status, complete, total:tasks.length, tasks };
}
