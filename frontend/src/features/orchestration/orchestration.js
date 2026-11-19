import { PHASE } from "../../shared/reducer.js";
import { escapeHtml } from "../../shared/dom.js";
import { branchOverview } from "./branchOverview.js";
import { mountStageConnections } from "./stageConnections.js";
import { mountWorkstreamCanvas, isPackageStage } from "./workstreamCanvas.js";

export const STEPS = [
  ["project", "定义需求", "项目目标、用户与功能", "01"],
  ["spec-ai", "规格 AI_Review", "DeepSeek 核对与补充", "02"],
  ["spec-human", "人工确认规格", "锁定范围与验收标准", "03"],
  ["tasks", "PM 任务分解", "开发任务与验收条件", "04"],
  ["flow", "执行编排", "依赖关系与并行批次", "05"],
  ["agents", "Agent 执行", "按批派发 · 人工可介入", "06"],
  ["review", "代码 AI_Review", "质量、逻辑与依赖检查", "07"],
  ["human", "人工 Review", "决定能否进入 QA / 交付", "08"],
];

const labels = { waiting: "待处理", active: "进行中", done: "已完成", blocked: "需介入" };
const roleName = (role) => ({ backend: "服务端", frontend: "界面", qa: "测试与验证" })[role] ?? role ?? "任务";
const taskLabel = (task) => `${task.id} · ${task.title ?? ""}`;
const brief = (task) => `<span><small>${escapeHtml(roleName(task.role))} · ${escapeHtml(task.id)}</small><strong title="${escapeHtml(task.title)}">${escapeHtml(task.title ?? task.id)}</strong></span>`;

function drawBranches(preview, detail, tasks, execution) {
  const { handoffs, joins, parallel } = branchOverview(tasks, execution?.batches);
  preview.hidden = detail.hidden = !tasks.length;
  if (!tasks.length) return;
  const join = joins.find(({ inputs }) => new Set(inputs.map((task) => task.role)).size > 1) ?? joins[0];
  const pair = handoffs[0];
  preview.innerHTML = `
    <header><div><h3>任务交叉路径</h3><p>${handoffs.length} 条跨角色依赖 · ${joins.length} 个分支汇合 · ${parallel.length} 个并行批次</p></div>
      <button type="button" data-branch-open>查看编排细节 →</button></header>
    ${join ? `<div class="orch-join"><div>${join.inputs.slice(0, 2).map(brief).join("")}
      ${join.inputs.length > 2 ? `<small>另有 ${join.inputs.length - 2} 条输入</small>` : ""}</div>
      <svg viewBox="0 0 50 64" width="42" height="56" aria-hidden="true"><path d="M1 12H14Q22 12 22 20V32M1 52H14Q22 52 22 44V32M22 32H46M40 26L46 32L40 38"/></svg>
      ${brief(join.task)}</div>` : pair ? `<div class="orch-join">${brief(pair.from)}<span aria-hidden="true">→</span>${brief(pair.to)}</div>`
      : "<p>当前任务没有跨角色交接或独立分支汇合。</p>"}`;
  detail.innerHTML = `<h2>分支与交接</h2>
    <p class="ws-meta">并行批次以资源排程为准；分支汇合表示独立依赖在后续任务相接。</p>
    <details ${joins.length ? "open" : ""}><summary>分支汇合（${joins.length}）</summary><ul>${joins.map(({ task, inputs }) =>
      `<li><strong>${escapeHtml(taskLabel(task))}</strong><br>等待 ${inputs.map(taskLabel).map(escapeHtml).join("；")}</li>`).join("")}</ul></details>
    <details><summary>跨角色依赖（${handoffs.length}）</summary><ul>${handoffs.map(({ from, to }) =>
      `<li>${escapeHtml(taskLabel(from))} → ${escapeHtml(taskLabel(to))}</li>`).join("")}</ul></details>
    <details><summary>实际并行批次（${parallel.length}）</summary><ul>${parallel.map(({ number, ids }) =>
      `<li>批次 ${number}：${ids.map(escapeHtml).join("、")}</li>`).join("")}</ul></details>`;
}

function statuses(state, run, review) {
  const current = review?.stale ? null : review?.current;
  const locked = state.spec?.locked;
  const compiled = state.execution && !state.stale && state.execution.compiled !== false;
  const confirmed = state.phase === PHASE.PLAN_CONFIRMED && !state.stale;
  const aiPassed = current?.automatic_decision === "passed";
  const humanBlocked = ["rejected", "stopped"].includes(current?.status) || current?.blocked_by === "human";
  const aiBlocked = ["changes_requested", "needs_attention", "retryable", "stopped"].includes(current?.status);
  const status = [
    locked || state.specText?.trim() ? "done" : "active",
    state.specReview ? "done" : state.busy && state.phase === PHASE.SPEC_DRAFT ? "active" : "waiting",
    locked ? "done" : state.extraction?.stage === "full" || state.specReview ? "blocked" : "waiting",
    state.plan && !state.stale ? "done" : state.phase === PHASE.PLANNING ? "active"
      : ["failed", "invalid_output", "needs_clarification"].includes(state.job?.status) ? "blocked" : "waiting",
    confirmed ? "done" : compiled ? "blocked" : state.validation?.errors?.length ? "blocked" : "waiting",
    run?.status === "done" ? "done" : run?.status === "running" ? "active"
      : ["paused", "failed"].includes(run?.status) ? "blocked" : "waiting",
    aiPassed ? "done" : aiBlocked ? "blocked" : current ? "active" : "waiting",
    review?.downstream_ready ? "done" : humanBlocked || current?.status === "awaiting_human" ? "blocked" : "waiting",
  ];
  // Archived confirmed specs do not imply that a DeepSeek review report exists.
  if (!state.specReview && locked) status[1] = "waiting";
  return status;
}

export function mountOrchestration(root, { onSelect, onClose }) {
  const select = (q) => root.querySelector(q);
  const map = select(".orch-nodes");
  const drawer = select(".orch-drawer");
  const expand = select("[data-canvas-expand]");
  map.innerHTML = STEPS.map(([key, title, detail, index]) => `
    <button type="button" class="orch-node" data-orch-step="${key}" aria-pressed="false">
      <span class="orch-node-meta"><span class="orch-node-index">${index}</span><span class="orch-node-state"></span></span>
      <strong>${title}</strong><span class="orch-node-description">${detail}</span>
    </button>`).join("");
  const cards = [...map.querySelectorAll("button")];
  const connections = mountStageConnections(map, cards, STEPS, select("[data-stage-links]"));
  const packages = mountWorkstreamCanvas(root, onSelect);
  const canvasTabs = [...root.querySelectorAll("[data-canvas-view]")];
  const selectView = (view) => {
    select("[data-package-map]").hidden = view !== "packages";
    select("[data-process-map]").hidden = view !== "stages";
    for (const tab of canvasTabs) tab.setAttribute("aria-pressed", String(tab.dataset.canvasView === view));
  };
  for (const tab of canvasTabs) tab.onclick = () => selectView(tab.dataset.canvasView);
  const setExpanded = (expanded) => {
    root.classList.toggle("is-expanded", expanded);
    expand.setAttribute("aria-pressed", String(expanded));
    select("[data-expand-label]").textContent = expanded ? "收起画布" : "展开画布";
    if (expanded) window.scrollTo(0, 0);
  };
  expand.onclick = () => setExpanded(!root.classList.contains("is-expanded"));
  map.onclick = (event) => {
    const button = event.target.closest("[data-orch-step]");
    if (button) onSelect(button.dataset.orchStep);
  };
  select("[data-drawer-close]").onclick = () => onClose(true);
  root.addEventListener("click", (event) => {
    if (event.target.closest("[data-branch-open]")) onSelect("flow");
    const stageLink = event.target.closest("[data-go-stage]");
    if (stageLink) onSelect(stageLink.dataset.goStage);
    if (event.target.closest("[data-package-discuss]")) select("[data-package-discussion]").scrollIntoView({ block:"start" });
    const link = event.target.closest("[data-focus-spec]");
    if (!link) return;
    setExpanded(false);
    onClose(false);
    const selectors = {
      source: "#spec-source",
      ai: '[data-view="spec"] button[data-action="extract"]:not(:disabled), #spec-source',
      human: '[data-view="spec"] button[data-action="confirm"]:not(:disabled), #spec-remaining',
    };
    const fields = [...root.querySelectorAll(selectors[link.dataset.focusSpec])];
    (fields.find((target) => target.tagName === "BUTTON") ?? fields[0])?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.key !== "Escape" || event.target.closest(".studio-sample-options")) return;
    if (root.classList.contains("is-expanded")) {
      setExpanded(false);
      expand.focus();
    } else if (!drawer.hidden) onClose(true);
  });
  const panels = [...drawer.querySelectorAll("[data-stage-panel]")];
  let selected = null;
  let branchTasks, branchExecution;

  return {
    getPackage: (key) => packages.get(key),
    show(key, returnFocus = false) {
      const group = packages.get(key);
      const step = group ? [key, group.title, group.summary, group.id] : STEPS.find(([name]) => name === key);
      const wasOpen = !drawer.hidden;
      drawer.hidden = !step;
      connections.select(key);
      packages.select(key);
      if (group) selectView("packages");
      for (const button of cards) button.setAttribute("aria-pressed", String(button.dataset.orchStep === key));
      if (!step) {
        if (returnFocus) {
          if (isPackageStage(selected)) packages.focus(selected.slice(8));
          else cards.find((card) => card.dataset.orchStep === selected)?.focus();
        }
        selected = null;
        return;
      }
      for (const panel of panels) panel.hidden = panel.dataset.stagePanel !== (group ? "package" : key === "human" ? "review" : key);
      select("[data-drawer-kicker]").textContent = group ? `项目工作包 · ${group.id}` : `阶段 ${step[3]} / 08`;
      select("[data-drawer-title]").textContent = step[1];
      if (selected !== key) select(".orch-drawer-body").scrollTop = 0;
      selected = key;
      if (!wasOpen) select("[data-drawer-close]").focus({ preventScroll: true });
    },
    update(state, run, review) {
      packages.update(state, run);
      select("[data-open-plan]").hidden = !state.plan;
      select("[data-package-confirm]").hidden = state.phase === PHASE.PLAN_CONFIRMED && !state.stale;
      const current = statuses(state, run, review);
      cards.forEach((card, index) => {
        card.dataset.status = current[index];
        const label = index === 1 && !state.specReview && state.spec?.locked ? "无报告" : labels[current[index]];
        card.querySelector(".orch-node-state").textContent = label;
      });
      const spec = state.spec?.draft ?? state.plan?.normalized_spec ?? state.plan?.spec;
      const product = typeof spec?.product === "string" ? spec.product : spec?.product?.name;
      select("[data-orch-project]").textContent = product || "定义项目后，从这里查看开发编排与审查进度。";
      select("[data-tasks-empty]").hidden = Boolean(state.plan);
      select("[data-flow-empty]").hidden = Boolean(state.plan);
      select("[data-orch-spec-review]").textContent = state.specReview?.summary ?? "当前工作台未载入规格审查报告。";
      if (branchTasks !== state.plan?.tasks || branchExecution !== state.execution) {
        branchTasks = state.plan?.tasks;
        branchExecution = state.execution;
        drawBranches(select("[data-branch-preview]"), select("[data-branch-detail]"), branchTasks ?? [], branchExecution);
      }
      const metrics = [
        ["开发任务", state.plan?.tasks?.length],
        ["依赖关系", state.execution?.edges?.length],
        ["执行批次", state.execution?.batches?.length],
        ["校验错误", state.validation?.errors?.length],
      ];
      select(".orch-metrics").innerHTML = metrics.map(([name, value]) =>
        `<div><span>${escapeHtml(name)}</span><strong>${value ?? "—"}</strong></div>`).join("");
    },
  };
}
