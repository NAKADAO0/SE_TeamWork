import { createApi, ApiError } from "./shared/api.js";
import { download } from "./shared/dom.js";
import { PHASE, PHASE_LABEL, initialState, reduce } from "./shared/reducer.js";
import { createStore } from "./shared/store.js";
import { mountBatchPanel } from "./features/task-editor/batchPanel.js";
import { mountTaskDetail } from "./features/task-editor/taskDetail.js";
import { mountTaskTable } from "./features/task-editor/taskTable.js";
import { mountGraph } from "./features/workflow-graph/graphCanvas.js";
import { canLink } from "./features/workflow-graph/canvasLinking.js";
import { mountSpecEditor } from "./features/spec-editor/specEditor.js";

const params = new URLSearchParams(location.search);
const api = createApi({ mode: params.get("api") === "http" ? "http" : "fixture", base: "." });
const store = createStore(initialState(), reduce);

let templates = [];

const phaseEl = document.querySelector('[data-role="phase"]');
const refsEl = document.querySelector('[data-role="refs"]');
const messageEl = document.querySelector('[data-role="message"]');
const versionsEl = document.querySelector('[data-role="versions"]');
const buttons = {
  validate: document.querySelector('[data-action="validate"]'),
  compile: document.querySelector('[data-action="compile"]'),
  confirm: document.querySelector('[data-action="confirm"]'),
  export: document.querySelector('[data-action="export"]'),
};

const actions = {
  loadSample,
  checkSpec,
  confirmSpec,
  startPlanning,
  setTemplate,
  setConfig,
  patchTask,
  addTask,
  deleteTask,
  link,
  unlink,
};

const views = {
  spec: mountSpecEditor(document.querySelector('[data-view="spec"]'), { store, actions }),
  table: mountTaskTable(document.querySelector('[data-view="table"]'), { store, actions }),
  detail: mountTaskDetail(document.querySelector('[data-view="detail"]'), { store, actions }),
  graph: mountGraph(document.querySelector('[data-view="graph"]'), { store, actions }),
  batches: mountBatchPanel(document.querySelector('[data-view="batches"]'), { store }),
};

document.body.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  if (target === buttons.validate) runValidation();
  if (target === buttons.compile) runCompile();
  if (target === buttons.confirm) runConfirm();
  if (target === buttons.export) runExport();
});

/* ---------- 状态与提示 ---------- */

function notice(level, text) {
  store.dispatch({ type: "MESSAGE", message: { level, text } });
}

function fail(error) {
  const text =
    error instanceof ApiError ? `${error.code}：${error.message}` : error?.message ?? String(error);
  store.dispatch({ type: "ERROR", message: text });
}

function currentDraft() {
  try {
    return JSON.parse(store.getState().specText);
  } catch (error) {
    fail(new Error(`规格不是合法 JSON：${error.message}`));
    return null;
  }
}

/* ---------- 规格 ---------- */

async function loadSample(name) {
  if (name !== "chat") {
    notice("warn", "内容社区样例尚未接入，先使用聊天 MVP 验证流程");
    return;
  }
  try {
    const draft = await api.loadSpecSample();
    store.dispatch({ type: "SPEC_LOADED", draft, source: name });
  } catch (error) {
    fail(error);
  }
}

async function checkSpec() {
  const draft = currentDraft();
  if (!draft) return;
  store.dispatch({ type: "SPEC_PARSED", draft });
  try {
    const checked = await api.submitSpec({ draft });
    store.dispatch({ type: "SPEC_CHECKED", issues: checked.issues });
    const { errors, warnings } = checked.issues;
    if (errors.length) notice("error", `发现 ${errors.length} 个规格缺口，先修复再确认`);
    else notice("info", `规格可确认：v${checked.version}，${warnings.length} 条警告`);
  } catch (error) {
    fail(error);
  }
}

async function confirmSpec() {
  const draft = currentDraft();
  if (!draft) return;
  store.dispatch({ type: "SPEC_PARSED", draft });
  try {
    const checked = await api.submitSpec({ draft });
    store.dispatch({ type: "SPEC_CHECKED", issues: checked.issues });
    if (checked.issues.errors.length) {
      notice("error", `还有 ${checked.issues.errors.length} 个规格缺口，先修复再确认`);
      return;
    }
    const confirmed = await api.confirmSpec({ specId: checked.spec_id, version: checked.version });
    store.dispatch({ type: "SPEC_CONFIRMED", ref: confirmed.spec_ref });
  } catch (error) {
    fail(error);
  }
}

/* ---------- 规划作业 ---------- */

async function startPlanning() {
  const state = store.getState();
  if (!state.spec?.locked) {
    notice("error", "请先确认规格，生成作业必须绑定确认过的输入快照");
    return;
  }
  try {
    const job = await api.createPlanningJob({
      spec_ref: state.spec.ref,
      template_id: state.templateId,
      config: store.getState().config,
      mode: api.mode === "fixture" ? "offline_rule" : "live_llm",
    });
    store.dispatch({ type: "JOB_STARTED", jobId: job.job_id });
    pollJob(job.job_id);
  } catch (error) {
    fail(error);
  }
}

async function pollJob(jobId) {
  try {
    const job = await api.getPlanningJob(jobId);
    store.dispatch({ type: "JOB_UPDATED", job });
    if (job.status === "succeeded") {
      const plan = await api.getPlan({
        planId: job.plan_ref.plan_id,
        revision: job.plan_ref.revision,
      });
      store.dispatch({ type: "JOB_SUCCEEDED", plan });
      return;
    }
    if (["failed", "invalid_output", "needs_clarification"].includes(job.status)) return;
    setTimeout(() => pollJob(jobId), 450);
  } catch (error) {
    fail(error);
  }
}

// Use the existing graph navigation; transfer only a compiled snapshot of the current revision.
const graphNavigation = document.querySelector('nav a[href="../graph.html"]');
graphNavigation?.addEventListener("click", (event) => {
  const state = store.getState();
  if (!state.plan) return;
  event.preventDefault();
  const current = !state.stale && state.execution && state.execution.compiled !== false &&
    [PHASE.PLAN_COMPILED, PHASE.PLAN_CONFIRMED].includes(state.phase) &&
    state.execution.plan_ref?.revision === state.plan.revision;
  if (!current) {
    notice("warn", "请先完成当前修订的编排，再查看图谱");
    return;
  }
  try {
    sessionStorage.setItem("se.plan", JSON.stringify({
      ...state.plan,
      source: "workspace",
      status: state.phase === PHASE.PLAN_CONFIRMED ? "confirmed" : "compiled",
      spec: state.spec?.draft ?? {},
      capacity: state.config.max_concurrency,
      batches: state.execution.batches,
    }));
    location.href = "../graph.html?sample=current";
  } catch (error) {
    fail(error);
  }
});

/* ---------- 校验、编排、确认、导出 ---------- */

async function runValidation() {
  const state = store.getState();
  if (!state.plan) {
    notice("warn", "先生成计划再校验");
    return;
  }
  try {
    const report = await api.validate({
      plan: state.plan,
      spec: state.spec?.draft,
      revision: state.plan.revision,
      planId: state.plan.plan_id,
    });
    store.dispatch({ type: "VALIDATED", report });
  } catch (error) {
    fail(error);
  }
}

async function runCompile() {
  const state = store.getState();
  if (!state.plan) {
    notice("warn", "先生成计划再编排");
    return;
  }
  try {
    const execution = await api.compile({
      plan: state.plan,
      config: state.config,
      revision: state.plan.revision,
      planId: state.plan.plan_id,
    });
    store.dispatch({ type: "COMPILED", execution });
  } catch (error) {
    fail(error);
  }
}

async function runConfirm() {
  const state = store.getState();
  if (!state.plan) return;
  try {
    const result = await api.confirm({
      planId: state.plan.plan_id,
      revision: state.plan.revision,
      execution_plan_id: state.execution?.plan_ref?.execution_plan_id,
    });
    store.dispatch({
      type: "CONFIRMED",
      revision: result.revision,
      execution_plan_id: result.execution_plan_id,
    });
  } catch (error) {
    fail(error);
  }
}

async function runExport() {
  const state = store.getState();
  if (!state.plan) {
    notice("warn", "没有可导出的计划");
    return;
  }
  try {
    const output = await api.exportPlan({
      plan: state.plan,
      execution: state.execution,
      validation: state.validation,
      spec: state.spec?.draft,
      planId: state.plan.plan_id,
      revision: state.plan.revision,
    });
    download(output.filename, output.json);
    notice("info", `已导出 ${output.filename}（修订 ${state.plan.revision}）`);
  } catch (error) {
    fail(error);
  }
}

/* ---------- 编辑 ---------- */

function patchTask(taskId, patch) {
  store.dispatch({ type: "TASK_PATCH", taskId, patch });
}

function addTask() {
  const plan = store.getState().plan;
  if (!plan) return;
  const index = plan.tasks.length + 1;
  store.dispatch({
    type: "TASK_ADD",
    task: {
      id: `T-${String(index).padStart(2, "0")}`,
      title: "新任务",
      description: "",
      kind: "implementation",
      stage: "implement",
      role: "backend",
      requirement_ids: [],
      module_ids: [],
      design_refs: [],
      inputs: [],
      outputs: [],
      acceptance: [""],
      depends_on: [],
      priority: 1,
    },
  });
  notice("warn", "已新增任务，请补写验收条件与依赖后再校验");
}

function deleteTask(taskId) {
  store.dispatch({ type: "TASK_DELETE", taskId });
}

function link(from, to) {
  const tasks = store.getState().plan?.tasks ?? [];
  const verdict = canLink(tasks, from, to);
  if (!verdict.ok) {
    notice("error", verdict.message);
    return;
  }
  store.dispatch({ type: "TASK_LINK", from, to });
}

function unlink(from, to) {
  store.dispatch({ type: "TASK_UNLINK", from, to });
}

function setTemplate(templateId) {
  const template = templates.find((item) => item.template_id === templateId);
  store.dispatch({
    type: "CONFIG_CHANGED",
    templateId,
    config: {
      max_concurrency: template?.max_concurrency ?? 2,
      role_capacity: template?.role_capacity ?? { backend: 1, frontend: 1, qa: 1 },
    },
  });
}

function setConfig(patch) {
  store.dispatch({ type: "CONFIG_CHANGED", config: patch });
}

/* ---------- 渲染 ---------- */

function renderVersions(state) {
  if (!state.history.length) {
    versionsEl.innerHTML = "";
    return;
  }
  versionsEl.innerHTML = `
    <span class="ws-meta">版本历史：</span>
    ${state.history
      .map(
        (entry) => `
        <button type="button" class="chip ${entry.phase === PHASE.PLAN_CONFIRMED ? "confirmed" : ""}"
          data-revision="${entry.revision}">r${entry.revision} ${entry.phase === PHASE.PLAN_CONFIRMED ? "已确认" : "草稿"}</button>`,
      )
      .join("")}
    <span class="ws-meta">点版本号可导出该修订，旧版本不会被覆盖</span>
  `;
}

versionsEl.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-revision]");
  if (!chip) return;
  const entry = store.getState().history.find((item) => item.revision === Number(chip.dataset.revision));
  if (!entry) return;
  download(
    `${store.getState().plan?.plan_id ?? "plan"}-r${entry.revision}.json`,
    JSON.stringify({ revision: entry.revision, phase: entry.phase, tasks: entry.tasks }, null, 2),
  );
});

function render(state) {
  phaseEl.textContent = PHASE_LABEL[state.phase] ?? state.phase;
  phaseEl.dataset.phase = state.phase;
  const parts = [
    state.spec?.ref ? `规格 ${state.spec.ref.spec_id} v${state.spec.ref.version}` : "规格未确认",
    state.plan ? `计划 ${state.plan.plan_id} r${state.plan.revision}` : "无计划",
    api.mode === "fixture" ? "离线规则（fixture）" : "实时模型（服务端）",
  ];
  if (state.stale) parts.push("待重新编排");
  refsEl.textContent = parts.join(" ｜ ");

  messageEl.textContent = state.message?.text ?? "";
  messageEl.className = `ws-message ${state.message?.level ?? ""}`;

  buttons.validate.disabled = !state.plan || state.busy;
  buttons.compile.disabled = !state.plan || !state.validation || state.busy;
  buttons.confirm.disabled = state.phase !== PHASE.PLAN_COMPILED || state.stale || state.busy;
  buttons.export.disabled = !state.plan;

  for (const view of Object.values(views)) view.update(state);
  renderVersions(state);
}

store.subscribe(render);
render(store.getState());

(async () => {
  try {
    templates = await api.loadTemplates();
    views.spec.setTemplates(templates);
    await actions.loadSample("chat");
  } catch (error) {
    fail(error);
  }
})();

window.__workspace = { store, api, actions };
