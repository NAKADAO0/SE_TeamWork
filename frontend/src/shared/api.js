/**
 * 工作台的数据访问层：界面只依赖这里的方法名与返回形状。
 *
 * - mode=fixture：读 frontend/fixtures，编辑只存在内存，用于无后端演示；
 * - mode=http：按设计文档 §7.1 的路径请求真实的 Python 服务。
 * 换后端时只改这一层，界面与 reducer 不动。
 */
import { gapCheck } from "../features/spec-editor/gapCheck.js";
import { schedule } from "./fixtureSchedule.js";
import { precheck } from "./precheck.js";

export class ApiError extends Error {
  constructor(code, text, details = {}) {
    super(text);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
  }
}

export function createApi({ mode = "fixture", base = "." } = {}) {
  return mode === "http" ? createHttpApi(base) : createFixtureApi(base);
}

function createFixtureApi(base) {
  const clone = (value) => JSON.parse(JSON.stringify(value));
  let plan = null;
  let polls = 0;

  async function readJson(path) {
    const response = await fetch(`${base}/${path}`);
    if (!response.ok) throw new ApiError("FIXTURE_MISSING", `无法读取 fixture：${path}`);
    return response.json();
  }

  async function loadPlan() {
    plan = clone(await readJson("fixtures/taskplan-chat.json"));
    return clone(plan);
  }

  return {
    mode: "fixture",
    async loadTemplates() {
      return readJson("fixtures/templates.json");
    },
    async loadSpecSample() {
      return readJson("fixtures/spec-chat.json");
    },
    async loadPlanSample() {
      polls = 0;
      return loadPlan();
    },
    async submitSpec({ draft }) {
      return {
        spec_id: draft?.spec_id ?? "SPEC-DRAFT",
        version: Number(draft?.version ?? 0) + 1,
        draft,
        issues: gapCheck(draft),
      };
    },
    async confirmSpec({ specId, version }) {
      return {
        spec_ref: { spec_id: specId, version: Number(version) },
        confirmed_at: new Date().toISOString(),
      };
    },
    async createPlanningJob({ spec_ref, template_id, config, mode }) {
      polls = 0;
      return { job_id: "JOB-LOCAL-1", status: "pending", spec_ref, template_id, config, mode };
    },
    async getPlanningJob(jobId) {
      polls += 1;
      if (polls === 1) return { job_id: jobId, status: "running" };
      if (!plan) await loadPlan();
      return {
        job_id: jobId,
        status: "succeeded",
        plan_ref: { plan_id: plan.plan_id, revision: plan.revision },
        generation_mode: plan.generation_mode,
      };
    },
    async getPlan({ planId, revision }) {
      if (!plan) await loadPlan();
      return { ...clone(plan), revision: Number(revision ?? plan.revision), plan_id: planId ?? plan.plan_id };
    },
    async savePlan({ planId, expected_revision, tasks }) {
      if (!plan) await loadPlan();
      if (plan.plan_id !== planId) throw new ApiError("NOT_FOUND", "计划不存在");
      if (Number(expected_revision) !== plan.revision) {
        throw new ApiError("REVISION_CONFLICT", "修订已过期，请刷新后重试", { current: plan.revision });
      }
      plan = { ...plan, tasks, revision: plan.revision + 1 };
      return { plan_id: plan.plan_id, revision: plan.revision };
    },
    async validate({ plan: current, spec, revision }) {
      const report = precheck({ plan: { ...current, revision }, spec });
      return { ...report, plan_ref: { plan_id: current.plan_id, revision } };
    },
    async compile({ plan: current, config, revision }) {
      const result = schedule(current.tasks, config);
      return {
        ...result,
        plan_ref: {
          plan_id: current.plan_id,
          revision,
          execution_plan_id: `EXE-${current.plan_id}-r${revision}`,
          digest: `draft-r${revision}-${current.tasks.length}t`,
        },
      };
    },
    async confirm({ planId, revision, execution_plan_id }) {
      return {
        plan_id: planId,
        revision,
        execution_plan_id,
        confirmed_at: new Date().toISOString(),
      };
    },
    async exportPlan({ plan: current, execution, validation, spec }) {
      const payload = {
        exported_at: new Date().toISOString(),
        exported_by: "member-1-workspace",
        revision: current.revision,
        spec_ref: current.spec_ref,
        template_ref: current.template_ref,
        generation_mode: current.generation_mode,
        spec,
        tasks: current.tasks,
        execution_plan: execution,
        validation_report: validation,
      };
      const lines = [
        `# ${spec?.product?.name ?? "计划"} · 修订 ${current.revision}`,
        "",
        `- 规格：${current.spec_ref?.spec_id ?? "-"} v${current.spec_ref?.version ?? "-"}`,
        `- 模板：${current.template_ref?.template_id ?? "-"}`,
        `- 生成模式：${current.generation_mode ?? "未标注"}`,
        `- 任务数：${current.tasks.length}`,
        `- 批次数：${execution?.batches?.length ?? 0}`,
        "",
        "| 任务 | 角色 | 前置 | 验收 |",
        "| --- | --- | --- | --- |",
        ...current.tasks.map((task) => {
          const deps = (task.depends_on ?? [])
            .map((dep) => (typeof dep === "string" ? dep : dep.task_id))
            .join("、") || "设计规格";
          const acceptance = Array.isArray(task.acceptance) ? task.acceptance.join("；") : task.acceptance;
          return `| ${task.id} ${task.title} | ${task.role} | ${deps} | ${acceptance} |`;
        }),
      ];
      return {
        json: JSON.stringify(payload, null, 2),
        markdown: lines.join("\n"),
        filename: `${current.plan_id}-r${current.revision}.json`,
      };
    },
  };
}

function createHttpApi(base) {
  async function request(method, path, body) {
    const response = await fetch(`${base}${path}`, {
      method,
      headers: body ? { "content-type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new ApiError(
        data?.code ?? `HTTP_${response.status}`,
        data?.message ?? `请求失败（${response.status}）`,
        data?.details ?? {},
      );
    }
    return data;
  }

  return {
    mode: "http",
    loadTemplates: () => request("GET", "/templates"),
    loadSpecSample: () => request("GET", "/specs/sample/chat"),
    loadPlanSample: () => request("GET", "/plans/sample/chat"),
    submitSpec: ({ draft }) => request("POST", "/specs", { draft }),
    confirmSpec: ({ specId, version }) =>
      request("POST", `/specs/${specId}/confirm`, { expected_version: version }),
    createPlanningJob: (body) => request("POST", "/planning-jobs", body),
    getPlanningJob: (jobId) => request("GET", `/planning-jobs/${jobId}`),
    getPlan: ({ planId, revision }) => request("GET", `/plans/${planId}?revision=${revision}`),
    savePlan: ({ planId, expected_revision, tasks }) =>
      request("PUT", `/plans/${planId}`, { expected_revision, tasks }),
    validate: ({ planId, revision }) => request("POST", `/plans/${planId}/validate`, { revision }),
    compile: ({ planId, revision, config }) =>
      request("POST", `/plans/${planId}/compile`, { revision, config }),
    confirm: ({ planId, revision, execution_plan_id }) =>
      request("POST", `/plans/${planId}/confirm`, { revision, execution_plan_id }),
    exportPlan: ({ planId, revision }) =>
      request("GET", `/plans/${planId}/export?revision=${revision}&format=json`),
  };
}
