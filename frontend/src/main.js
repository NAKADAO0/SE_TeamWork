import { splitSpecText } from "./shared/specText.js";
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
import { gapCheck, parseSpecMarkdownClient } from "./features/spec-editor/gapCheck.js";

const params = new URLSearchParams(location.search);
let api = createApi({ mode: "fixture", base: "." });
const sessionKey = `se.workspace.v1:${params.get("api") ?? "auto"}`;
let restored = null;
try {
  const saved = JSON.parse(sessionStorage.getItem(sessionKey));
  if (saved?.state && !saved.state.busy && Object.values(PHASE).includes(saved.state.phase)) restored = saved;
} catch {
  // 浏览器禁用存储或旧快照损坏时正常打开空白工作台。
}
const store = createStore({ ...initialState(), ...restored?.state }, reduce);

let templates = [];
let liveLlmConfigured = false;

const phaseEl = document.querySelector('[data-role="phase"]');
const refsEl = document.querySelector('[data-role="refs"]');
const messageEl = document.querySelector('[data-role="message"]');
const versionsEl = document.querySelector('[data-role="versions"]');
const stages = [...document.querySelectorAll("[data-stage]")];
const panels = [...document.querySelectorAll("[data-stage-panel]")];
let stage = restored?.state?.plan && ["spec", "tasks", "flow"].includes(restored.stage) ? restored.stage : "spec";

function saveSession() {
  const state = store.getState();
  if (state.busy) return;
  try {
    sessionStorage.setItem(sessionKey, JSON.stringify({ state, stage }));
  } catch {
    // 存储空间不足时仍允许正常编辑与导航。
  }
}

function showStage(next) {
  stage = next;
  for (const button of stages) {
    if (button.dataset.stage === next) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  for (const panel of panels) panel.hidden = panel.dataset.stagePanel !== next;
  saveSession();
}

for (const button of stages) {
  button.addEventListener("click", () => showStage(button.dataset.stage));
}
const graphLink = document.querySelector('[data-action="open-graph"]');
const buttons = {
  workflow: document.querySelector('[data-action="run-workflow"]'),
  save: document.querySelector('[data-action="save"]'),
  validate: document.querySelector('[data-action="validate"]'),
  compile: document.querySelector('[data-action="compile"]'),
  confirm: document.querySelector('[data-action="confirm"]'),
  export: document.querySelector('[data-action="export"]'),
  exportMd: document.querySelector('[data-action="export-md"]'),
};

const actions = {
  loadSample,
  importSpecText,
  checkSpec,
  extractSpec,
  reopenScope,
  generateRemainingSpec,
  confirmSpec,
  startPlanning,
  setTemplate,
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
  if (target === buttons.workflow) startPlanning(true);
  if (target === buttons.save) runSave();
  if (target === buttons.validate) runValidation();
  if (target === buttons.compile) runCompile();
  if (target === buttons.confirm) runConfirm();
  if (target === buttons.export) runExport("json");
  if (target === buttons.exportMd) runExport("markdown");
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

function parseSpecInput() {
  const raw = store.getState().specText ?? "";
  const trimmed = raw.trim();
  if (!trimmed) {
    fail(new Error("设计规格不能为空，请导入或输入 JSON / Markdown 规格"));
    return null;
  }
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        fail(new Error("设计规格 JSON 必须是对象"));
        return null;
      }
      return { draft: parsed, text: null, parsedPreview: parsed };
    } catch (error) {
      fail(new Error(`规格不是合法 JSON：${error.message}`));
      return null;
    }
  }
  return { draft: null, text: raw, parsedPreview: parseSpecMarkdownClient(raw) };
}

/* ---------- 规格 ---------- */

async function loadSample(name) {
  const state = store.getState();
  if (state.busy || state.spec?.locked) return;
  if (state.specText.trim() && !window.confirm("用这份样例替换当前说明书？")) return;
  try {
    const sample = await api.loadSpecSample(name);
    const current = store.getState();
    if (current.spec?.locked || current.busy || current.specText !== state.specText) return;
    store.dispatch({ type: "SPEC_LOADED", draft: sample.draft, text: sample.text, source: name });
  } catch (error) {
    fail(error);
  }
}

function importSpecText(content, filename = "") {
  const trimmed = String(content ?? "").trim();
  if (!trimmed) {
    notice("error", "导入文件为空");
    return;
  }
  if (trimmed.startsWith("{")) {
    try {
      const draft = JSON.parse(trimmed);
      store.dispatch({ type: "SPEC_LOADED", draft, text: trimmed, source: filename || "import" });
      notice("info", `已导入 JSON 规格：${filename || "自定义文件"}`);
      return;
    } catch (error) {
      notice("error", `JSON 解析失败：${error.message}`);
      return;
    }
  }
  const draft = parseSpecMarkdownClient(trimmed);
  store.dispatch({ type: "SPEC_LOADED", draft, text: trimmed, source: filename || "markdown" });
  if (gapCheck(draft).errors.length && api.mode === "http" && liveLlmConfigured) {
    extractSpec();
  } else {
    notice("info", `已导入设计说明书：${filename || "自定义文档"}`);
  }
}

async function extractSpec() {
  const state = store.getState();
  const source = (state.sourceText || state.extraction?.source || state.specText).trim();
  if (state.busy || state.spec?.locked || !source) return;
  if (api.mode !== "http" || !liveLlmConfigured) {
    notice("error", "AI 整理需要已配置实时模型的后端。");
    return;
  }
  store.dispatch({ type: "EXTRACTION_BUSY", busy: true });
  notice("info", "DeepSeek 正在整理产品范围和明确排除项。");
  try {
    const result = await api.extractSpec(source, { stage: "scope" });
    if (store.getState().sourceText !== state.sourceText || store.getState().specText !== state.specText) return;
    views.spec.showSource(state.specText);
    store.dispatch({
      type: "SPEC_LOADED", draft: result.normalized, text: result.text, source: "ai-extraction",
      extraction: { stage: "scope", source },
    });
    store.dispatch({ type: "SPEC_CHECKED", issues: result.issues });
    const missing = result.issues.errors.length;
    notice(missing ? "warn" : "info", missing
      ? `已整理出草稿，还有 ${missing} 处缺口。请对照原文补充后再确认。`
      : "范围已整理。可直接修改，确认后再生成其余规格。");
  } catch (error) {
    if (error instanceof ApiError && error.code === "HTTP_404") {
      notice("error", "当前后端版本缺少 AI 整理接口。请在项目根目录重新启动后端，再打开对应端口的工作台。");
    } else {
      fail(error);
    }
  } finally {
    store.dispatch({ type: "EXTRACTION_BUSY", busy: false });
  }
}

function reopenScope() {
  const state = store.getState();
  if (state.busy || !state.extraction?.confirmedScope) return;
  store.dispatch({ type: "SCOPE_TEXT_CHANGE", text: state.specText });
  notice("warn", "修改范围后需重新生成其余规格和计划。");
}

function checkScope() {
  const state = store.getState();
  const parsed = parseSpecMarkdownClient((state.specParts ?? splitSpecText(state.specText)).scope);
  const issues = gapCheck(parsed);
  const scopeIssues = {
    errors: issues.errors.filter((item) => ["MISSING_PRODUCT", "MISSING_SCOPE"].includes(item.code)),
    warnings: [],
  };
  store.dispatch({ type: "SPEC_CHECKED", issues: scopeIssues });
  return scopeIssues.errors.length === 0;
}

async function generateRemainingSpec() {
  const state = store.getState();
  if (state.busy || !checkScope()) return;
  store.dispatch({ type: "EXTRACTION_BUSY", busy: true });
  notice("info", "正在根据已确认范围生成其余规格。");
  try {
    const scope = (state.specParts ?? splitSpecText(state.specText)).scope;
    const source = state.sourceText || state.extraction?.source || scope;
    const result = await api.extractSpec(source, {
      stage: "full", confirmed_scope: scope,
    });
    store.dispatch({
      type: "SPEC_LOADED", draft: result.normalized, text: result.text, source: "ai-extraction",
      extraction: { ...state.extraction, source, stage: "full", confirmedScope: scope },
    });
    store.dispatch({ type: "SPEC_CHECKED", issues: result.issues });
    notice(result.issues.errors.length ? "warn" : "info", result.issues.errors.length
      ? `规格已生成，还有 ${result.issues.errors.length} 处缺口，请补充后确认。`
      : "规格已生成。请核对 AI 补充假设，再确认规格。");
  } catch (error) {
    fail(error);
  } finally {
    store.dispatch({ type: "EXTRACTION_BUSY", busy: false });
  }
}

async function checkSpec() {
  if (store.getState().extraction?.stage === "scope") {
    if (checkScope()) notice("info", "范围可以确认，确认后将生成其余规格。");
    return;
  }
  const parsed = parseSpecInput();
  if (!parsed) return;
  store.dispatch({ type: "SPEC_PARSED", draft: parsed.parsedPreview });
  try {
    const checked = await api.submitSpec({ draft: parsed.draft, text: parsed.text });
    if (checked.normalized) {
      store.dispatch({ type: "SPEC_PARSED", draft: checked.normalized });
    }
    store.dispatch({ type: "SPEC_CHECKED", issues: checked.issues });
    const { errors, warnings } = checked.issues;
    if (errors.length && parsed.text && api.mode === "http" && liveLlmConfigured &&
        store.getState().specText.trim() === parsed.text.trim() &&
        store.getState().spec?.source !== "ai-extraction") {
      await extractSpec();
      return;
    }
    if (errors.length) notice("error", `发现 ${errors.length} 个规格缺口，先修复再确认`);
    else notice("info", `规格可确认：v${checked.version}，${warnings.length} 条警告`);
  } catch (error) {
    fail(error);
  }
}

async function confirmSpec() {
  if (store.getState().extraction?.stage === "scope") {
    notice("warn", "请先确认范围并生成其余规格");
    return;
  }
  const parsed = parseSpecInput();
  if (!parsed) return;
  store.dispatch({ type: "SPEC_PARSED", draft: parsed.parsedPreview });
  try {
    const checked = await api.submitSpec({ draft: parsed.draft, text: parsed.text });
    if (checked.normalized) {
      store.dispatch({ type: "SPEC_PARSED", draft: checked.normalized });
    }
    store.dispatch({ type: "SPEC_CHECKED", issues: checked.issues });
    if (checked.issues.errors.length && parsed.text && api.mode === "http" && liveLlmConfigured &&
        store.getState().specText.trim() === parsed.text.trim() &&
        store.getState().spec?.source !== "ai-extraction") {
      await extractSpec();
      return;
    }
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

async function startPlanning(visualize = false) {
  const state = store.getState();
  if (!state.spec?.locked) {
    notice("error", "请先确认规格，生成作业必须绑定确认过的输入快照");
    return;
  }
  if (api.mode !== "http" || !liveLlmConfigured) {
    notice("error", "实时 PM 未配置，请通过已配置模型的后端打开工作台。");
    return;
  }
  try {
    const job = await api.createPlanningJob({
      spec_ref: state.spec.ref,
      template_id: state.templateId,
      config: state.config,
      mode: "live_llm",
    });
    store.dispatch({ type: "JOB_STARTED", jobId: job.job_id });
    pollJob(job.job_id, visualize);
  } catch (error) {
    fail(error);
  }
}

async function pollJob(jobId, visualize) {
  try {
    const job = await api.getPlanningJob(jobId);
    const active = store.getState().job;
    if ((active?.job_id ?? active?.id) !== jobId) return;
    store.dispatch({ type: "JOB_UPDATED", job });
    if (job.status === "succeeded") {
      const plan = await api.getPlan({
        planId: job.plan_ref.plan_id,
        revision: job.plan_ref.revision,
      });
      if (store.getState().job?.job_id !== jobId) return;
      store.dispatch({ type: "JOB_SUCCEEDED", plan });
      showStage("tasks");
      if (visualize && await runValidation() && await runCompile()) {
        showStage("flow");
        graphLink.focus({preventScroll: true});
        notice("info", "编排已完成，可在下方查看批次，或打开完整图谱。");
      }
      return;
    }
    if (["failed", "invalid_output", "needs_clarification"].includes(job.status)) return;
    setTimeout(() => pollJob(jobId, visualize), 350);
  } catch (error) {
    fail(error);
  }
}

/* ---------- 图谱跳转 ---------- */

for (const selector of ['nav a[href="../graph.html"]', '[data-action="open-graph"]']) {
  const linkEl = document.querySelector(selector);
  linkEl?.addEventListener("click", async (event) => {
    const state = store.getState();
    if (!state.plan) return;
    event.preventDefault();
    const current =
      !state.stale &&
      state.execution &&
      state.execution.compiled !== false &&
      [PHASE.PLAN_COMPILED, PHASE.PLAN_CONFIRMED].includes(state.phase) &&
      state.execution.plan_ref?.revision === state.plan.revision;
    if (!current) {
      notice("warn", "请先完成当前修订的编排，再查看完整工作流图谱");
      return;
    }
    if (api.mode === "http") {
      const apiArg = api.base || "http";
      location.href = `../graph.html?api=${encodeURIComponent(apiArg)}&plan_id=${encodeURIComponent(state.plan.plan_id)}&revision=${state.plan.revision}`;
      return;
    }
    try {
      sessionStorage.setItem(
        "se.plan",
        JSON.stringify({
          ...state.plan,
          source: "workspace",
          status: state.phase === PHASE.PLAN_CONFIRMED ? "confirmed" : "compiled",
          spec: state.spec?.draft ?? {},
          capacity: state.config.max_concurrency,
          batches: state.execution.batches,
        }),
      );
      location.href = "../graph.html?sample=current";
    } catch (error) {
      fail(error);
    }
  });
}

/* ---------- 保存修订、校验、编排、确认、导出 ---------- */

async function syncDraftIfNeeded({ silent = false } = {}) {
  const state = store.getState();
  if (api.mode !== "http" || !state.plan) return state.plan?.revision;
  if (state.persistedRevision === state.plan.revision) return state.plan.revision;

  const saved = await api.savePlan({
    planId: state.plan.plan_id,
    expected_revision: state.persistedRevision ?? 1,
    tasks: state.plan.tasks,
    config: state.config,
  });
  store.dispatch({ type: "PLAN_SYNCED", revision: saved.revision, silent });
  return saved.revision;
}

async function runSave() {
  try {
    await syncDraftIfNeeded({ silent: false });
  } catch (error) {
    fail(error);
  }
}

async function runValidation() {
  const state = store.getState();
  if (!state.plan) {
    notice("warn", "先生成计划再校验");
    return;
  }
  try {
    const revision = await syncDraftIfNeeded({ silent: true });
    const latest = store.getState();
    const report = await api.validate({
      plan: latest.plan,
      spec: latest.spec?.draft,
      revision,
      planId: latest.plan.plan_id,
    });
    store.dispatch({ type: "VALIDATED", report });
    return !(report.errors?.length);
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
    const revision = await syncDraftIfNeeded({ silent: true });
    const latest = store.getState();
    const execution = await api.compile({
      plan: latest.plan,
      config: latest.config,
      revision,
      planId: latest.plan.plan_id,
    });
    store.dispatch({ type: "COMPILED", execution });
    if (execution.compiled !== false) showStage("flow");
    return execution.compiled !== false;
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
      execution_plan_id: state.execution?.plan_ref?.execution_plan_id ?? state.execution?.execution_plan_id,
    });
    store.dispatch({
      type: "CONFIRMED",
      revision: result.revision ?? state.plan.revision,
      execution_plan_id: result.execution_plan_id,
    });
  } catch (error) {
    fail(error);
  }
}

async function runExport(format = "json") {
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
    if (format === "markdown") {
      download(output.mdFilename ?? `${state.plan.plan_id}-r${state.plan.revision}.md`, output.markdown || "");
      notice("info", `已导出 Markdown 清单（修订 ${state.plan.revision}）`);
    } else {
      download(output.filename, output.json);
      notice("info", `已导出 ${output.filename}（修订 ${state.plan.revision}）`);
    }
  } catch (error) {
    fail(error);
  }
}

/* ---------- 编辑 ---------- */

function patchTask(taskId, patch) {
  store.dispatch({ type: "TASK_PATCH", taskId, patch });
}

function addTask() {
  const state = store.getState();
  const plan = state.plan;
  if (!plan) return;
  const existing = new Set(plan.tasks.map((t) => t.id));
  let index = plan.tasks.length + 1;
  let id = `T-NEW-${String(index).padStart(2, "0")}`;
  while (existing.has(id)) {
    index += 1;
    id = `T-NEW-${String(index).padStart(2, "0")}`;
  }
  const defaultReqId = state.spec?.draft?.requirements?.[0]?.id;
  store.dispatch({
    type: "TASK_ADD",
    task: {
      id,
      title: "新任务",
      description: "人工补充的开发或验证任务",
      kind: "implementation",
      stage: "implement",
      role: "backend",
      requirement_ids: defaultReqId ? [defaultReqId] : [],
      module_ids: [],
      design_refs: [],
      inputs: [],
      outputs: [{ id: `O-${id}`, type: "implementation", description: "新任务产物" }],
      acceptance: [""],
      depends_on: [],
      priority: 1,
      estimated_effort: { value: 4, unit: "人时", basis: "人工新增任务估算" },
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

versionsEl.addEventListener("click", async (event) => {
  const chip = event.target.closest("[data-revision]");
  if (!chip) return;
  const rev = Number(chip.dataset.revision);
  const state = store.getState();
  if (api.mode === "http" && state.plan?.plan_id) {
    try {
      const remotePlan = await api.getPlan({ planId: state.plan.plan_id, revision: rev });
      download(`${state.plan.plan_id}-r${rev}.json`, JSON.stringify(remotePlan, null, 2));
      return;
    } catch {
      // 未保存到服务端的本地草稿回退到本地历史快照
    }
  }
  const entry = state.history.find((item) => item.revision === rev);
  if (!entry) return;
  download(
    `${state.plan?.plan_id ?? "plan"}-r${entry.revision}.json`,
    JSON.stringify({ revision: entry.revision, phase: entry.phase, tasks: entry.tasks }, null, 2),
  );
});

const MODE_LABEL = {
  live_llm: "实时 PM",
  offline_rule: "规则分解",
  fixture_replay: "样例回放",
  curated_example: "预编排案例",
};

function render(state) {
  phaseEl.textContent = PHASE_LABEL[state.phase] ?? state.phase;
  phaseEl.dataset.phase = state.phase;
  const dirty = api.mode === "http" && state.plan && state.persistedRevision !== state.plan.revision;
  const modeLabel = MODE_LABEL[state.plan?.generation_mode ?? state.generationMode] ?? "实时模型";
  const parts = [
    state.spec?.ref ? `规格 ${state.spec.ref.spec_id} v${state.spec.ref.version}` : "规格未确认",
    state.plan ? `计划 ${state.plan.plan_id} r${state.plan.revision}` : "无计划",
    api.mode === "fixture" ? "演示模式" : liveLlmConfigured ? "DeepSeek 接入" : `后端已连接 · ${modeLabel}`,
  ];
  if (dirty) parts.push("有未保存编辑");
  if (state.stale) parts.push("待重新编排");
  refsEl.textContent = parts.join(" ｜ ");

  messageEl.textContent = state.message?.text ?? "";
  messageEl.className = `ws-message ${state.message?.level ?? ""}`;

  buttons.workflow.disabled = !state.spec?.locked || state.busy || api.mode === "fixture";
  buttons.workflow.title = api.mode === "fixture" ? "请通过后端服务打开工作台" : "分解任务、校验并编排";
  for (const button of stages) {
    button.disabled = button.dataset.stage !== "spec" && !state.plan;
  }
  if (!state.plan && stage !== "spec") showStage("spec");
  buttons.save.hidden = api.mode !== "http" || !state.plan;
  buttons.save.disabled = !dirty || state.busy;
  buttons.validate.disabled = !state.plan || state.busy;
  buttons.compile.disabled = !state.plan || !state.validation || state.busy;
  buttons.confirm.disabled = state.phase !== PHASE.PLAN_COMPILED || state.stale || state.busy;
  buttons.export.disabled = !state.plan;
  buttons.exportMd.disabled = !state.plan;

  for (const view of Object.values(views)) view.update(state);
  renderVersions(state);
}

store.subscribe((state) => {
  render(state);
  saveSession();
});
render(store.getState());
showStage(stage);

async function resolveApi() {
  const apiParam = params.get("api");
  if (apiParam === "fixture") return createApi({ mode: "fixture", base: "." });
  if (apiParam === "http") {
    const base = location.port === "8010" || !location.port ? "" : "http://127.0.0.1:8010";
    return createApi({ mode: "http", base });
  }
  if (apiParam && /^https?:\/\//i.test(apiParam)) {
    return createApi({ mode: "http", base: apiParam });
  }
  // 无参数时探测同源后端 /health（由 uvicorn 托管时自动切到真实后端）
  try {
    const probe = await fetch("/health");
    if (probe.ok) {
      const data = await probe.json();
      if (data?.status === "ok") {
        liveLlmConfigured = Boolean(data.live_llm_configured);
        return createApi({ mode: "http", base: "" });
      }
    }
  } catch {
    // 静态文件服务器环境回退到 fixture 模式
  }
  return createApi({ mode: "fixture", base: "." });
}

(async () => {
  try {
    api = await resolveApi();
    if (api.mode === "http") {
      try {
        const health = await api.loadHealth();
        liveLlmConfigured = Boolean(health?.live_llm_configured);
      } catch {
        // ignore
      }
    }
    templates = await api.loadTemplates();
    views.spec.setTemplates(templates);
    render(store.getState());
    window.__workspace = { store, api, actions };
  } catch (error) {
    fail(error);
  }
})();
