/**
 * 工作台的数据访问层：界面只依赖这里的方法名与返回形状。
 *
 * - mode=fixture：读 frontend/fixtures，编辑只存在内存，用于无后端演示；
 * - mode=http：请求真实的 Python FastAPI 服务（支持同源 / 跨端口）。
 * 换后端时只改这一层，界面与 reducer 不动。
 */
import { gapCheck, parseSpecMarkdownClient } from "../features/spec-editor/gapCheck.js";
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

  async function readText(path) {
    const response = await fetch(`${base}/${path}`);
    if (!response.ok) throw new ApiError("FIXTURE_MISSING", `无法读取样例：${path}`);
    return response.text();
  }

  async function loadPlan() {
    plan = clone(await readJson("fixtures/taskplan-chat.json"));
    return clone(plan);
  }

  return {
    mode: "fixture",
    base,
    async loadHealth() {
      return { status: "ok", live_llm_configured: false, modes: ["offline_rule"] };
    },
    async loadTemplates() {
      return readJson("fixtures/templates.json");
    },
    async loadSpecSample(name = "chat") {
      const samples = new Map([
        ["chat", "contracts/chat/design-spec.md"],
        ["community", "contracts/community/design-spec.md"],
        ["unseen", "unseen-spec/spec.md"],
        ["custom", "design-spec-template.md"],
      ]);
      if (!samples.has(name)) throw new ApiError("SAMPLE_NOT_FOUND", "样例规格不存在");
      const text = await readText(`../examples/${samples.get(name)}`);
      return { name, format: "markdown", draft: parseSpecMarkdownClient(text), text };
    },
    async loadPlanSample() {
      polls = 0;
      return loadPlan();
    },
    async submitSpec({ draft, text }) {
      const parsed = draft ?? parseSpecMarkdownClient(text ?? "");
      return {
        spec_id: parsed?.spec_id ?? "SPEC-DRAFT",
        version: Number(parsed?.version ?? 0) + 1,
        draft: parsed,
        issues: gapCheck(parsed),
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
        throw new ApiError("REVISION_CONFLICT", "修订已过期，请刷新后重试", { current_revision: plan.revision });
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
      const prodName = typeof spec?.product === "string" ? spec.product : spec?.product?.name ?? "计划";
      const lines = [
        `# ${prodName} · 修订 ${current.revision}`,
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
        mdFilename: `${current.plan_id}-r${current.revision}.md`,
      };
    },
  };
}

function createHttpApi(base) {
  const prefix = String(base ?? "").replace(/\/$/, "");

  async function request(method, path, body) {
    let response;
    try {
      response = await fetch(`${prefix}${path}`, {
        method,
        headers: body !== undefined ? { "content-type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch {
      throw new ApiError("NETWORK_ERROR", "无法连接后端计划服务，请确认服务已启动");
    }
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = data?.detail;
      const detailObj = detail && typeof detail === "object" && !Array.isArray(detail) ? detail : data ?? {};
      const code =
        data?.code ??
        detailObj?.code ??
        (response.status === 409 ? "REVISION_CONFLICT" : `HTTP_${response.status}`);
      const msg =
        (typeof detail === "string" ? detail : null) ??
        data?.message ??
        detailObj?.message ??
        (response.status === 409 && detailObj?.current_revision
          ? `修订已过期，服务端最新修订为 r${detailObj.current_revision}`
          : `请求失败（${response.status}）`);
      throw new ApiError(code, msg, detailObj);
    }
    return data;
  }

  return {
    mode: "http",
    base: prefix,
    getConfirmedBundle: (planId, revision) => request("GET",
      `/plans/${encodeURIComponent(planId)}/export?revision=${encodeURIComponent(revision)}&format=json`),
    getReview: (planId, revision) =>
      request("GET", `/plans/${encodeURIComponent(planId)}/review?revision=${encodeURIComponent(revision)}`),
    updateReview: (planId, action, body) =>
      request("POST", `/plans/${encodeURIComponent(planId)}/review${action ? `/${action}` : ""}`, body),
    loadHealth: () => request("GET", "/health"),
    loadTemplates: () => request("GET", "/templates"),
    loadSpecSample: (name = "chat") => request("GET", `/specs/sample/${encodeURIComponent(name)}`),
    async loadPlanSample() {
      const res = await request("GET", "/plans/chat-demo");
      return res.plan ?? res;
    },
    extractSpec: (text) => request("POST", "/specs/review", { text }),
    submitSpec: ({ draft, text }) =>
      request("POST", "/specs", draft !== undefined && draft !== null ? { draft } : { text }),
    confirmSpec: ({ specId, version }) =>
      request("POST", `/specs/${encodeURIComponent(specId)}/confirm`, { expected_version: version }),
    createPlanningJob: ({ spec_ref, template_id, config, mode }) =>
      request("POST", "/planning-jobs", {
        spec_ref,
        template_ref: `${template_id || "default"}@1`,
        template_id,
        config,
        mode,
      }),
    getPlanningJob: (jobId) => request("GET", `/planning-jobs/${encodeURIComponent(jobId)}`),
    async getPlan({ planId, revision }) {
      const query = revision ? `?revision=${encodeURIComponent(revision)}` : "";
      const res = await request("GET", `/plans/${encodeURIComponent(planId)}${query}`);
      const plan = res.plan ?? res;
      return { ...plan, plan_id: res.plan_id ?? plan.plan_id, revision: res.revision ?? plan.revision };
    },
    savePlan: ({ planId, expected_revision, tasks, config }) =>
      request("PUT", `/plans/${encodeURIComponent(planId)}`, { expected_revision, tasks, config }),
    validate: ({ planId, revision }) =>
      request("POST", `/plans/${encodeURIComponent(planId)}/validate`, { revision }),
    async compile({ planId, revision, config }) {
      const data = await request("POST", `/plans/${encodeURIComponent(planId)}/compile`, { revision, config });
      const batchDetails =
        data.batch_details ??
        (data.batches ?? []).map((tasks, index) => ({
          index,
          tasks: Array.isArray(tasks) ? tasks : tasks.tasks ?? [],
          bindings: Array.isArray(tasks) ? {} : tasks.bindings ?? {},
          waiting: Array.isArray(tasks) ? [] : tasks.waiting ?? [],
        }));
      return {
        ...data,
        batches: batchDetails,
        raw_batches: data.batches,
      };
    },
    confirm: ({ planId, revision, execution_plan_id }) =>
      request("POST", `/plans/${encodeURIComponent(planId)}/confirm`, { revision, execution_plan_id }),
    async exportPlan({ planId, revision }) {
      const [jsonResp, mdResp] = await Promise.all([
        fetch(`${prefix}/plans/${encodeURIComponent(planId)}/export?revision=${encodeURIComponent(revision)}&format=json`),
        fetch(`${prefix}/plans/${encodeURIComponent(planId)}/export?revision=${encodeURIComponent(revision)}&format=markdown`),
      ]);
      if (!jsonResp.ok) {
        const err = await jsonResp.json().catch(() => null);
        throw new ApiError(`HTTP_${jsonResp.status}`, err?.detail ?? "请先确认当前修订再导出");
      }
      const json = await jsonResp.text();
      const markdown = mdResp.ok ? await mdResp.text() : "";
      return {
        json,
        markdown,
        filename: `${planId}-r${revision}.json`,
        mdFilename: `${planId}-r${revision}.md`,
      };
    },
  };
}
