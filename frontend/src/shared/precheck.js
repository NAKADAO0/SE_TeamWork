/**
 * 前端预检（非权威）。
 *
 * 目的：在没有后端时也能驱动界面，把设计文档 §5.1 的检查面在浏览器里先跑一遍。
 * 规则以成员 3 的 backend/app/validation 为权威实现；本文件在接入真实接口后
 * 只用于即时提示，不再阻断提交。
 */
import { dependencyIds, findCycle } from "./graph.js";

const issue = (severity, code, objectId, fieldPath, text, related = [], action = "") => ({
  severity,
  code,
  object_id: objectId,
  field_path: fieldPath,
  message: text,
  related_ids: related,
  suggested_action: action,
});

export function precheck({ plan, spec }) {
  const blocking = [];
  const warnings = [];
  const tasks = plan?.tasks ?? [];
  const byId = new Map();

  for (const task of tasks) {
    if (byId.has(task.id)) {
      blocking.push(
        issue("error", "DUPLICATE_TASK_ID", task.id, "tasks[].id", `任务编号 ${task.id} 重复`, [task.id], "合并或重命名重复任务"),
      );
    }
    byId.set(task.id, task);
  }

  for (const task of tasks) {
    for (const dep of task.depends_on ?? []) {
      const id = typeof dep === "string" ? dep : dep?.task_id;
      if (!id) continue;
      if (id === task.id) {
        blocking.push(
          issue("error", "SELF_DEPENDENCY", task.id, "depends_on", `任务 ${task.id} 依赖自己`, [task.id], "删除自依赖"),
        );
      } else if (!byId.has(id)) {
        blocking.push(
          issue("error", "MISSING_DEPENDENCY", task.id, "depends_on", `前置任务 ${id} 不存在`, [id], "补建前置任务或删除该依赖"),
        );
      }
    }

    const acceptance = Array.isArray(task.acceptance) ? task.acceptance : [task.acceptance ?? ""];
    if (!acceptance.some((item) => String(item).trim())) {
      blocking.push(
        issue("error", "MISSING_ACCEPTANCE", task.id, "acceptance", `任务 ${task.id} 缺少可检查的验收条件`, [task.id], "补写验收条件"),
      );
    }

    for (const input of task.inputs ?? []) {
      if (input?.source === "task_output") {
        const [producerId, outputId] = String(input.ref ?? "").split("/");
        const producer = byId.get(producerId);
        const found = producer?.outputs?.some((output) => output.id === outputId);
        if (!found) {
          blocking.push(
            issue(
              "error",
              "UNRESOLVED_INPUT",
              task.id,
              "inputs",
              `输入 ${input.ref} 找不到生产任务或产物`,
              [producerId].filter(Boolean),
              "改为指向已存在的任务产物，或补上直接依赖",
            ),
          );
        }
      } else if (input?.source === "design" && spec?.design) {
        if (!spec.design.some((entry) => entry.id === input.ref)) {
          blocking.push(
            issue("error", "UNRESOLVED_DESIGN_REF", task.id, "design_refs", `设计引用 ${input.ref} 不在规格中`, [], "补规格条目或修正引用"),
          );
        }
      }
    }
  }

  const cycle = findCycle(tasks);
  if (cycle) {
    blocking.push(
      issue(
        "error",
        "DEPENDENCY_CYCLE",
        cycle[0],
        "depends_on",
        `存在依赖环：${cycle.join(" → ")}`,
        cycle,
        "删除环上任意一条边",
      ),
    );
  }

  for (const requirement of spec?.requirements ?? []) {
    const related = tasks.filter((task) => (task.requirement_ids ?? []).includes(requirement.id));
    if (!related.some((task) => task.kind === "implementation")) {
      blocking.push(
        issue(
          "error",
          "REQUIREMENT_WITHOUT_IMPLEMENTATION",
          requirement.id,
          `requirements[${requirement.id}]`,
          `需求 ${requirement.id} 没有实现任务`,
          related.map((task) => task.id),
          "补一项实现任务",
        ),
      );
    }
    if (!related.some((task) => task.kind === "verification")) {
      blocking.push(
        issue(
          "error",
          "REQUIREMENT_WITHOUT_VERIFICATION",
          requirement.id,
          `requirements[${requirement.id}]`,
          `需求 ${requirement.id} 没有验证任务`,
          related.map((task) => task.id),
          "补一项验证任务",
        ),
      );
    }
  }

  for (const task of tasks) {
    if (dependencyIds(task).length === 0 && (task.kind === "verification" || task.kind === "integration")) {
      warnings.push(
        issue("warning", "UNGROUNDED_VERIFICATION", task.id, "depends_on", `验证类任务 ${task.id} 没有前置实现`, [task.id], "确认是否应等待实现产物"),
      );
    }
  }

  return {
    plan_ref: { plan_id: plan?.plan_id ?? "", revision: plan?.revision ?? 0 },
    blocking,
    warnings,
    checked_at: new Date().toISOString(),
  };
}
