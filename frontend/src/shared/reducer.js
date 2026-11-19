/**
 * 工作台状态机（成员 1 负责）。
 *
 * 纯函数：不读 DOM、不发请求、不看时间。所有界面状态变化都必须经过这里，
 * 这样 V09（编辑后要重算、旧版仍可查）与 V10（陈旧修订）才能被单元测试覆盖。
 */

export const PHASE = Object.freeze({
  SPEC_DRAFT: "spec_draft",
  SPEC_CONFIRMED: "spec_confirmed",
  PLANNING: "planning",
  PLAN_DRAFT: "plan_draft",
  PLAN_VALIDATED: "plan_validated",
  PLAN_COMPILED: "plan_compiled",
  PLAN_CONFIRMED: "plan_confirmed",
});

export const PHASE_LABEL = Object.freeze({
  spec_draft: "规格草稿",
  spec_confirmed: "规格已确认",
  planning: "生成中",
  plan_draft: "计划草稿",
  plan_validated: "已校验",
  plan_compiled: "已编排",
  plan_confirmed: "已确认",
});

export function initialState() {
  return {
    phase: PHASE.SPEC_DRAFT,
    specText: "",
    specToken: 0,
    spec: null,
    specIssues: null,
    templateId: "default",
    generationMode: "live_llm",
    config: { max_concurrency: 2, role_capacity: { backend: 1, frontend: 1, qa: 1 } },
    job: null,
    plan: null,
    persistedRevision: null,
    history: [],
    validation: null,
    execution: null,
    selectedTaskId: null,
    stale: false,
    message: null,
    busy: false,
  };
}

const message = (level, text) => ({ level, text });

const snapshot = (plan, phase) => ({
  revision: plan.revision,
  phase,
  task_count: plan.tasks.length,
  tasks: plan.tasks,
});

function pushHistory(history, plan, phase) {
  const next = [...history.filter((item) => item.revision !== plan.revision), snapshot(plan, phase)];
  return next.slice(-20);
}

function invalidatePlan(state, plan, text, level = "info") {
  const wasConfirmed = state.phase === PHASE.PLAN_CONFIRMED;
  return {
    ...state,
    plan,
    history: pushHistory(state.history, plan, PHASE.PLAN_DRAFT),
    phase: PHASE.PLAN_DRAFT,
    validation: null,
    execution: null,
    stale: true,
    message: message(
      level,
      wasConfirmed ? "确认版保持只读，已派生新草稿；需要重新校验与编排" : text,
    ),
  };
}

function replaceTask(plan, taskId, patch) {
  return plan.tasks.map((task) => (task.id === taskId ? { ...task, ...patch } : task));
}

export function reduce(state, action) {
  switch (action.type) {
    case "SPEC_AI_REVIEW":
      return { ...state, specReview: action.report ?? null };
    case "SPEC_TEXT_CHANGE": {
      const spec = state.spec ? { ...state.spec, locked: false } : null;
      return {
        ...state,
        specReview: null,
        specText: action.text,
        spec,
        specIssues: null,
        phase: PHASE.SPEC_DRAFT,
        stale: Boolean(state.plan),
      };
    }

    case "SPEC_CHECKED":
      return { ...state, specIssues: action.issues };

    case "SPEC_PARSED":
      return state.spec ? { ...state, spec: { ...state.spec, draft: action.draft } } : state;

    case "CONFIG_CHANGED": {
      const config = { ...state.config, ...(action.config ?? {}) };
      if (action.config?.role_capacity) {
        config.role_capacity = { ...state.config.role_capacity, ...action.config.role_capacity };
      }
      return {
        ...state,
        templateId: action.templateId ?? state.templateId,
        config,
        execution: null,
        stale: Boolean(state.plan),
        phase: state.plan
          ? state.validation
            ? PHASE.PLAN_VALIDATED
            : PHASE.PLAN_DRAFT
          : state.phase,
        message: state.plan
          ? message("warn", "模板或容量已修改，已作废当前编排，需要重新编排")
          : state.message,
      };
    }

    case "MODE_CHANGED":
      return { ...state, generationMode: action.mode };

    case "SPEC_LOADED": {
      const draft = action.draft;
      const productName = typeof draft?.product === "string" ? draft.product : draft?.product?.name;
      return {
        ...state,
        specReview: null,
        specText: action.text ?? JSON.stringify(draft, null, 2),
        specToken: state.specToken + 1,
        spec: { draft, locked: false, ref: null, source: action.source ?? "" },
        specIssues: null,
        phase: PHASE.SPEC_DRAFT,
        stale: Boolean(state.plan),
        message: message("info", `已载入规格：${productName || "未命名产品"}`),
      };
    }

    case "SPEC_REOPEN": {
      if (!state.spec) return state;
      return {
        ...state,
        specReview: null,
        spec: { ...state.spec, locked: false },
        phase: PHASE.SPEC_DRAFT,
        message: message("warn", "已解除确认，可继续修改；再次确认会生成新的规格版本"),
      };
    }

    case "SPEC_CONFIRMED": {
      if (!state.spec) return state;
      const ref = action.ref;
      return {
        ...state,
        spec: { ...state.spec, locked: true, ref },
        phase: PHASE.SPEC_CONFIRMED,
        stale: Boolean(state.plan),
        message: message("info", `规格 ${ref.spec_id} v${ref.version} 已确认，输入快照冻结`),
      };
    }

    case "JOB_STARTED":
      return {
        ...state,
        phase: PHASE.PLANNING,
        busy: true,
        job: { id: action.jobId, status: "running" },
        message: message("info", "已提交规划作业，等待 PM 返回"),
      };

    case "JOB_UPDATED": {
      const job = action.job;
      if (job.status === "needs_clarification") {
        const gaps = (job.gaps ?? []).map((g) => ({
          severity: "error",
          code: g.code ?? "GAP",
          field_path: g.field_path ?? "",
          message: g.message ?? "",
        }));
        return {
          ...state,
          busy: false,
          job,
          spec: state.spec ? { ...state.spec, locked: false } : state.spec,
          specIssues: gaps.length ? { errors: gaps, warnings: [] } : state.specIssues,
          phase: PHASE.SPEC_DRAFT,
          message: message("warn", job.error?.message ?? "规格存在关键缺口，请补充后重新提交"),
        };
      }
      if (job.status === "failed" || job.status === "invalid_output") {
        const detail = (job.issues ?? []).slice(0, 3).map((i) => `${i.code}: ${i.message}`).join("；");
        return {
          ...state,
          busy: false,
          job,
          phase: state.spec?.locked ? PHASE.SPEC_CONFIRMED : PHASE.SPEC_DRAFT,
          message: message("error", [job.error?.message ?? `规划作业失败：${job.status}`, detail].filter(Boolean).join(" — ")),
        };
      }
      return { ...state, job };
    }

    case "RESTORE_CONFIRMED_PLAN": {
      const bundle = action.bundle;
      const plan = bundle.plan;
      const draft = plan.normalized_spec ?? plan.spec ?? {};
      const execution = { ...bundle.execution, batches: bundle.execution.batch_details ??
        bundle.execution.batches.map((tasks,index)=>({ index,tasks,bindings:{},waiting:[] })) };
      return { ...initialState(), plan, execution, validation: bundle.validation,
        persistedRevision: bundle.revision, phase: PHASE.PLAN_CONFIRMED,
        history: [snapshot(plan, PHASE.PLAN_CONFIRMED)],
        spec: { locked:true, ref:plan.spec_ref, draft }, specText: JSON.stringify(draft,null,2),
        specToken: state.specToken+1, selectedTaskId:plan.tasks?.[0]?.id,
        message:message("info", `已打开已确认快照 ${bundle.plan_id} r${bundle.revision}`) };
    }

    case "PLAN_LOADED":
    case "JOB_SUCCEEDED": {
      const plan = action.plan;
      return {
        ...state,
        plan,
        persistedRevision: plan?.revision ?? 1,
        busy: false,
        job: state.job ? { ...state.job, status: "succeeded" } : null,
        history: [snapshot(plan, PHASE.PLAN_DRAFT)],
        phase: PHASE.PLAN_DRAFT,
        validation: null,
        execution: null,
        stale: false,
        selectedTaskId: plan?.tasks?.[0]?.id ?? null,
        message: message("info", `已载入计划 ${plan.plan_id} 修订 ${plan.revision}`),
      };
    }

    case "PLAN_SYNCED": {
      if (!state.plan) return state;
      const revision = action.revision ?? state.plan.revision;
      const syncedPlan = { ...state.plan, revision };
      return {
        ...state,
        plan: syncedPlan,
        persistedRevision: revision,
        history: pushHistory(state.history.filter((h) => h.revision !== state.plan.revision || h.revision === revision), syncedPlan, state.phase),
        message: action.silent ? state.message : message("info", `已保存修订 r${revision} 到数据库`),
      };
    }

    case "TASK_PATCH": {
      if (!state.plan) return state;
      const tasks = replaceTask(state.plan, action.taskId, action.patch);
      const plan = { ...state.plan, tasks, revision: state.plan.revision + 1 };
      return invalidatePlan(state, plan, `任务 ${action.taskId} 已修改，需要重新校验与编排`);
    }

    case "TASK_LINK": {
      if (!state.plan) return state;
      const { from, to } = action;
      const target = state.plan.tasks.find((task) => task.id === to);
      if (!target) return state;
      const deps = target.depends_on ?? [];
      if (deps.some((dep) => (typeof dep === "string" ? dep : dep.task_id) === from)) {
        return { ...state, message: message("warn", `依赖 ${from} → ${to} 已存在`) };
      }
      const tasks = replaceTask(state.plan, to, {
        depends_on: [...deps, { task_id: from, reason: "界面连线添加的依赖" }],
      });
      const plan = { ...state.plan, tasks, revision: state.plan.revision + 1 };
      return invalidatePlan(state, plan, `已添加依赖 ${from} → ${to}，需要重新校验与编排`);
    }

    case "TASK_UNLINK": {
      if (!state.plan) return state;
      const { from, to } = action;
      const target = state.plan.tasks.find((task) => task.id === to);
      if (!target) return state;
      const tasks = replaceTask(state.plan, to, {
        depends_on: (target.depends_on ?? []).filter(
          (dep) => (typeof dep === "string" ? dep : dep.task_id) !== from,
        ),
      });
      const plan = { ...state.plan, tasks, revision: state.plan.revision + 1 };
      return invalidatePlan(state, plan, `已删除依赖 ${from} → ${to}，需要重新校验与编排`);
    }

    case "TASK_ADD": {
      if (!state.plan) return state;
      const task = action.task;
      const plan = {
        ...state.plan,
        tasks: [...state.plan.tasks, task],
        revision: state.plan.revision + 1,
      };
      return {
        ...invalidatePlan(state, plan, `已新增任务 ${task.id}，需要重新校验与编排`),
        selectedTaskId: task.id,
      };
    }

    case "TASK_DELETE": {
      if (!state.plan) return state;
      const target = state.plan.tasks.find((task) => task.id === action.taskId);
      if (!target) return state;
      let removed = 0;
      const tasks = state.plan.tasks
        .filter((task) => task.id !== action.taskId)
        .map((task) => {
          const deps = (task.depends_on ?? []).filter((dep) => {
            const id = typeof dep === "string" ? dep : dep.task_id;
            const drop = id === action.taskId;
            if (drop) removed += 1;
            return !drop;
          });
          return { ...task, depends_on: deps };
        });
      const plan = { ...state.plan, tasks, revision: state.plan.revision + 1 };
      const next = invalidatePlan(
        state,
        plan,
        `已删除任务 ${action.taskId}，同时移除 ${removed} 条依赖，需要重新校验与编排`,
        "warn",
      );
      return {
        ...next,
        selectedTaskId: tasks[0]?.id ?? null,
      };
    }

    case "VALIDATED": {
      if (!state.plan) return state;
      const report = action.report;
      const blocking = report.blocking ?? [];
      return {
        ...state,
        validation: report,
        phase: blocking.length ? PHASE.PLAN_DRAFT : PHASE.PLAN_VALIDATED,
        message: blocking.length
          ? message("error", `校验发现 ${blocking.length} 个阻断问题，修正后才能编排`)
          : message("info", `校验通过：${report.warnings?.length ?? 0} 条警告需要在确认时说明`),
      };
    }

    case "COMPILED": {
      if (!state.plan) return state;
      const execution = action.execution;
      const revision = state.plan.revision;
      const mismatch =
        !state.validation ||
        state.validation.plan_ref?.revision !== revision ||
        execution.plan_ref?.revision !== revision;
      if (mismatch) {
        return {
          ...state,
          execution: null,
          phase: state.validation ? PHASE.PLAN_VALIDATED : PHASE.PLAN_DRAFT,
          message: message("error", "校验报告或编排结果不属于当前修订，请重新校验与编排"),
        };
      }
      if (execution.compiled === false) {
        return {
          ...state,
          execution,
          phase: PHASE.PLAN_VALIDATED,
          message: message(
            "error",
            `仍有 ${execution.blocked?.length ?? 0} 个任务无法安排，检查依赖或角色容量`,
          ),
        };
      }
      return {
        ...state,
        execution,
        phase: PHASE.PLAN_COMPILED,
        stale: false,
        message: message(
          "info",
          `已编排：${execution.batches?.length ?? 0} 个批次，${execution.nodes?.length ?? 0} 个节点`,
        ),
      };
    }

    case "CONFIRMED": {
      if (!state.plan || !state.execution) return state;
      if (state.stale) {
        return { ...state, message: message("error", "任务已修改，必须先重新编排再确认") };
      }
      if (state.execution.plan_ref?.revision !== state.plan.revision) {
        return { ...state, message: message("error", "编排结果不是当前修订，请重新编排") };
      }
      if (action.revision !== state.plan.revision) {
        return {
          ...state,
          message: message("error", `提交的修订 ${action.revision} 已过期，当前为 ${state.plan.revision}`),
        };
      }
      return {
        ...state,
        phase: PHASE.PLAN_CONFIRMED,
        history: pushHistory(state.history, state.plan, PHASE.PLAN_CONFIRMED),
        message: message("info", `修订 ${state.plan.revision} 已确认，成为只读版本`),
      };
    }

    case "SELECT_TASK":
      return { ...state, selectedTaskId: action.taskId };

    case "MESSAGE":
      return { ...state, message: action.message };

    case "EXTRACTION_BUSY":
      return { ...state, busy: action.busy };

    case "ERROR":
      return { ...state, busy: false, message: message("error", action.message) };

    default:
      return state;
  }
}
