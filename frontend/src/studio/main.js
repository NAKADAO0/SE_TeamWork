import { escapeHtml as h, download } from "../shared/dom.js";
import {
  proposalsMarkup,
  discussionMarkup,
  specMarkup,
  editorMarkup,
  collectSpec,
  questionMarkup,
} from "./content.js";

import { executionGraph, graphFeedback } from "./executionGraph.js";
import { mountExecutionWorkspace } from "./executionWorkspace.js";
import { roleName, taskDetail } from "./taskViews.js";
const $ = (id) => document.getElementById(id);
const executionWorkspace = mountExecutionWorkspace($("execution-workspace"));
let resourceDraft = null;
const resourceKey = () => viewed?.plan_ref ? `${viewed.id}:${viewed.plan_ref.plan_id}:${viewed.plan_ref.revision}:${viewed.execution?.execution_plan_id}` : null;
const resourceDirty = () => resourceDraft?.key === resourceKey() && resourceDraft.dirty;
const labels = {
  ready: "待启动",
  running: "运行中",
  pausing: "等待当前调用结束后暂停",
  paused: "已暂停",
  failed: "执行失败",
  awaiting_confirmation: "等待人工确认",
  needs_input: "需要你的决定",
  needs_resolution: "待处理明确冲突",
  complete: "执行计划已确认",
  confirming: "正在确认计划",
};
const nodeLabels = {
  collaborate: "角色方案",
  generate: "汇总方案",
  artifact: "产物版本",
  review: "独立审查",
  confirm: "人工确认与决策",
  spec_adapter: "整理设计规格",
  plan: "生成任务计划",
  validate: "契约与依赖校验",
  compile: "资源编排",
};
const eventLabels = {
  "run.created": "创建运行",
  "node.started": "节点开始",
  "node.completed": "节点完成",
  "node.failed": "节点失败",
  "collaboration.started": "协作开始",
  "artifact.created": "产物更新",
  "review.completed": "审查完成",
  "interaction.requested": "请求人工介入",
  "workflow.feedback": "反馈修订",
  "plan.created": "任务计划已生成",
  "run.failed": "运行失败",
  "run.paused": "已暂停",
  "run.interrupted": "服务重启",
  "command.start": "启动",
  "command.resume": "继续",
  "command.pause": "请求暂停",
  "command.confirm": "人工确认",
  "command.reassess": "重新检查推进条件",
  "command.step": "单步执行",
  "command.accept_risk": "人工接受风险",
  "plan.confirmed": "执行计划已确认",
  "compile.started": "重新计算执行批次",
  "compile.failed": "执行批次计算失败",
  "plan.recompiled": "资源配置与批次已更新",
};
let definition,
  liveRun,
  viewed,
  events = [],
  selectedStage,
  selectedNode = "collaborate",
  selectedTask,
  busy = false,
  replaying = false,
  timer,
  plan;
const provisionalAcknowledged = new Set();
let editing = false,
  editBase;
let rid = new URL(location.href).searchParams.get("run");
let feedbackVisible = false,
  follow = true;
const plans = new Map();
const planForView = () =>
  viewed?.plan_ref
    ? plans.get(viewed.plan_ref.plan_id + ":" + viewed.plan_ref.revision)
    : null;

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.detail === "string"
        ? value.detail
        : JSON.stringify(value.detail ?? value),
    );
  return value;
}
function notice(text = "") {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}
const pretty = (value) => JSON.stringify(value, null, 2);
const code = (value) =>
  `<pre>${h(typeof value === "string" ? value : pretty(value))}</pre>`;
const list = (items) =>
  items?.length
    ? `<ul>${items.map((x) => `<li>${h(x)}</li>`).join("")}</ul>`
    : "";
const stageLabel = (id) =>
  definition.stages.find((s) => s.id === id)?.label ?? id;
const stageInfo = () => viewed.stages[selectedStage] ?? {};
function nodeExecutions(node) {
  return viewed.executions.filter(
    (e) => e.stage_id === selectedStage && e.node_id === node,
  );
}
function nodeStatus(node) {
  if (node === "confirm")
    return stageInfo().confirmed ||
      (selectedStage === "confirmation" && viewed.status === "complete")
      ? "succeeded"
      : viewed.stage === selectedStage &&
          viewed.status === "awaiting_confirmation"
        ? "running"
        : "waiting";
  return nodeExecutions(node).at(-1)?.status ?? "waiting";
}
function stageStatus(stage) {
  if (stage === "input") return "done";
  if (viewed.stages[stage]?.confirmed) return "done";
  if (stage === "planning" && viewed.plan_ref) return "done";
  if (stage === "confirmation" && viewed.status === "complete") return "done";
  if (stage === "execution" && viewed.status === "complete") return "active";
  if (stage === viewed.stage)
    return [
      "needs_input",
      "needs_resolution",
      "failed",
      "awaiting_confirmation",
      "paused",
    ].includes(viewed.status)
      ? "blocked"
      : "active";
  return "waiting";
}
function render() {
  if (!viewed) return;
  document.body.dataset.runStatus = viewed.status;
  document.body.dataset.stage = selectedStage;
  $("workspace").hidden = false;
  $("toggle-events").disabled = false;
  $("intake").hidden = true;
  $("project-name").textContent =
    viewed.spec.product ||
    viewed.stages[viewed.stage]?.draft?.spec?.product ||
    "新项目工作流";
  $("run-caption").textContent =
    `${viewed.id} · ${viewed.input_mode === "spec" ? "已有规格" : "原始需求"} · ${replaying ? "历史状态" : "实时观察"}`;
  $("stages").innerHTML = definition.stages
    .map(
      (s, index) =>
        `<button type="button" class="stage" data-stage="${s.id}" data-status="${stageStatus(s.id)}" ${s.id === selectedStage ? 'aria-current="step"' : ""}><i class="stage-symbol" aria-hidden="true">${stageStatus(s.id) === "done" ? '<svg viewBox="0 0 16 16" width="11" height="11" fill="none"><path d="m3 8 3 3 7-7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>' : index + 1}</i><strong>${h(s.label)}</strong><span>${{ done: "已完成", active: "进行中", blocked: "需介入", waiting: "待处理" }[stageStatus(s.id)]}</span></button>`,
    )
    .join("");
  $("run-status").textContent = labels[viewed.status] ?? viewed.status;
  $("run-failure").hidden = viewed.status !== "failed";
  $("run-failure").innerHTML =
    viewed.status === "failed"
      ? `<strong>执行故障：${h(nodeLabels[viewed.executions.filter((item) => item.status === "failed").at(-1)?.node_id] ?? "当前节点")}</strong><p>${h(viewed.error ?? "请查看运行事件中的失败原因")}</p><small>可重试失败节点；已完成结果会复用。这不是要求你补充需求。</small>`
      : "";
  const active = viewed.executions.filter((e) => e.status === "running").length;
  $("monitor").textContent =
    `${Object.values(viewed.stages).filter((s) => s.confirmed).length} / 3 阶段已确认 · ${active} 个活跃调用 · ${viewed.feedback.length} 次反馈`;
  const commands = [];
  if (!replaying) {
    if (["ready", "paused", "failed"].includes(viewed.status)) {
      commands.push('<button data-command="step">单步执行</button>');
    }
    if (["ready", "paused", "failed"].includes(viewed.status))
      commands.push(
        `<button class="primary" data-command="${viewed.status === "ready" ? "start" : "resume"}">${viewed.status === "ready" ? "启动" + stageLabel(viewed.stage) : viewed.status === "failed" ? "重试失败节点" : "继续运行"}</button>`,
      );
    if (viewed.status === "running")
      commands.push('<button data-command="pause">暂停运行</button>');
    if (viewed.status === "awaiting_confirmation")
      commands.push(
        viewed.stage === "confirmation" && (selectedStage !== "confirmation" || selectedNode !== "confirm")
          ? '<button class="primary" data-open-final>核对并确认计划</button>'
          : `<button class="primary" data-command="confirm">确认${viewed.stage === "confirmation" ? "执行计划" : "本阶段产物"}</button>`,
      );
  }
  $("controls").innerHTML = commands.join("");
  $("controls")
    .querySelectorAll("button")
    .forEach(
      (b) =>
        (b.disabled =
          busy ||
          (b.dataset.command === "confirm" && viewed.stage === "confirmation" && (resourceDirty() || viewed.execution?.compiled === false)) ||
          (b.dataset.command === "confirm" &&
            viewed.stages[viewed.stage]?.provisional &&
            !provisionalAcknowledged.has(ackKey()))),
    );
  $("stage-title").textContent = stageLabel(selectedStage);
  document.querySelector(".runbar-context").textContent = ["execution", "delivery"].includes(selectedStage) ? "开发与交付" : "规划与设计";
  $("stage-caption").textContent =
    ({planning: "查看任务职责与开发范围。", confirmation: "设置执行资源，计算批次后确认计划。", execution: "查看工作包与任务关系，手动开始执行。", delivery: "检查开发产物，完成代码审查与人工审核。"})[selectedStage]
      ?? `按职责出案与讨论${stageInfo().round ? " · 第 " + stageInfo().round + " 轮审查" : ""}`;
  const limit = viewed.stage_budgets?.[selectedStage] ?? stageInfo().budget;
  if (limit)
    $("stage-caption").textContent +=
      ` · 人工答疑 ${limit.used}/${limit.limit} 轮`;
  const cross = viewed.feedback.filter((f) => f.scope === "cross_stage").at(-1);
  $("feedback").hidden =
    !cross ||
    (!feedbackVisible && viewed.stages[cross.target_stage]?.confirmed);
  if (cross)
    $("feedback").textContent =
      `跨阶段修订：${stageLabel(cross.source_stage)} 返回 ${stageLabel(cross.target_stage)}。${cross.reason}`;
  renderGlobalFeedback();
  const needsDecision =
    selectedStage === viewed.stage &&
    (["needs_input", "needs_resolution"].includes(viewed.status) ||
      stageInfo().provisional);
  $("stage-alert").hidden = !needsDecision || selectedNode === "confirm";
  $("stage-alert").innerHTML = needsDecision
    ? stageInfo().provisional
      ? "<span>AI 已形成暂定方案，请核对选择与影响后最终确认。</span><button data-open-confirm>核对 AI 暂定事项</button>"
      : "<span>本阶段需要你的决定。可选择 AI 建议，也可自由补充。</span><button data-open-confirm>处理待确认问题</button>"
    : "";
  renderSteps();
  renderGraph();
  renderDetail();
  renderStageActions();
  renderDag();
  const executionStage = ["execution", "delivery"].includes(selectedStage);
  const currentPlan = planForView();
  const confirmed = viewed.status === "complete";
  executionWorkspace.update(executionStage && currentPlan && viewed.execution ? {plan: {...currentPlan.plan, ...viewed.plan_ref}, execution: viewed.execution, stage: selectedStage, readonly: replaying, confirmed} : null);
  $("details").hidden = executionStage && Boolean(currentPlan && viewed.execution);
  document.querySelector(".document-heading").hidden = executionStage && Boolean(currentPlan && viewed.execution);
  $("toggle-flow").hidden = executionStage;
  if (executionStage) $("flow-structure").hidden = true;
  renderEvents();
}
function renderGlobalFeedback() {
  const cross = viewed.feedback.filter((f) => f.scope === "cross_stage");
  const active = cross.filter(
    (f) =>
      !viewed.stages[f.target_stage]?.confirmed && viewed.status !== "complete",
  );
  const shown = feedbackVisible ? cross : active.slice(-1);
  $("global-feedback").hidden = !shown.length;
  if (!shown.length) return;
  const nodeLabel = (stage, node) =>
    definition.subworkflows[stage]?.nodes.find((item) => item.id === node)
      ?.label;
  $("global-feedback").innerHTML = shown
    .map((item) => {
      const sourceNode = nodeLabel(item.source_stage, item.source_node);
      const targetNode = nodeLabel(item.target_stage, item.target_node);
      return `<span>${h(stageLabel(item.source_stage))}${sourceNode ? " / " + h(sourceNode) : ""} 返回 ${h(stageLabel(item.target_stage))}${targetNode ? " / " + h(targetNode) : ""}<small>${h(item.reason ?? "")}</small></span>`;
    })
    .join("");
}
function preferredNode(stage) {
  if (stage === "confirmation")
    return viewed?.execution ? "compile" : "validate";
  if (stage === "planning") return viewed?.plan_ref ? "plan" : "spec_adapter";
  return viewed?.stages[stage]?.draft ? "generate" : "collaborate";
}
function renderSteps() {
  const flow = definition.subworkflows[selectedStage];
  const labels = {
    collaborate: "角色方案",
    generate: "汇总方案",
    review: "审查结果",
    confirm: "人工确认与决策",
  };
  const nodes = flow ? [...flow.nodes] : [];
  if (["requirements", "product", "architecture"].includes(selectedStage))
    nodes.splice(1, 0, { id: "discussion", label: "讨论与决策" });
  $("stage-steps").innerHTML = nodes
    .map(
      (node) =>
        `<button type="button" class="step-tab" data-node="${node.id}" data-status="${node.id === "discussion" ? (stageInfo().draft?.decisions?.length ? "succeeded" : "waiting") : nodeStatus(node.id)}" aria-pressed="${selectedNode === node.id}"><span class="step-status" aria-hidden="true"></span>${h(selectedStage === "confirmation" ? ({validate:"计划校验",compile:"资源配置与排程",confirm:"确认执行计划"}[node.id] ?? node.label) : labels[node.id] ?? nodeLabels[node.id] ?? node.label)}</button>`,
    )
    .join("");
}
function renderGraph() {
  if ($("flow-structure").hidden) {
    $("canvas").innerHTML = "";
    return;
  }
  const flow = definition.subworkflows[selectedStage];
  if (!flow) {
    $("canvas").innerHTML =
      `<div class="empty"><strong>${selectedStage === "input" ? "输入已保存" : "后续执行入口保留"}</strong><p>${selectedStage === "input" ? "查看本页保存的原始输入。" : "本轮不自动执行代码开发与交付。"}</p></div>`;
    return;
  }
  const width = Math.max(240, $("canvas").clientWidth - 24);
  const feedback = graphFeedback(viewed, selectedStage, feedbackVisible);
  $("canvas").innerHTML = executionGraph(
    flow,
    feedback,
    width,
    nodeStatus,
    selectedTask ? null : selectedNode,
  );
}
function renderDetail() {
  $("detail-title").textContent = selectedTask && selectedStage !== "planning"
    ? "任务详情"
    : (nodeLabels[selectedNode] ?? "节点详情");
  $("detail-state").textContent = replaying ? "历史只读" : "";
  if (selectedTask && selectedStage !== "planning") {
    const task = planForView()?.plan?.tasks.find((t) => t.id === selectedTask);
    $("details").innerHTML = task
      ? `<h3>${h(task.title)}</h3><p>${h(task.id)} · ${h(task.role)}</p><p>${h(task.description)}</p><h3>验收条件</h3>${list(Array.isArray(task.acceptance) ? task.acceptance : [task.acceptance])}<h3>依赖与交接</h3>${code(task.depends_on)}<h3>输入与产物</h3>${code({ inputs: task.inputs, outputs: task.outputs })}`
      : code(selectedTask);
    return;
  }
  if (selectedStage === "input") {
    $("details").innerHTML = `<h3>原始输入</h3>${code(viewed.source)}`;
    return;
  }
  if (["execution", "delivery"].includes(selectedStage)) {
    $("details").innerHTML =
      "<p>完成任务规划并确认计划后，可在这里查看工作包、任务图谱和执行产物。</p>";
    return;
  }
  if (selectedStage === "confirmation") {
    renderResources();
    return;
  }
  const info = stageInfo();
  const executions = nodeExecutions(selectedNode);
  const latest = executions.at(-1);
  let content = "";
  if (editing && editBase) {
    $("details").innerHTML = editorMarkup(editBase, selectedStage);
    return;
  }
  if (selectedNode === "collaborate") {
    content = proposalsMarkup(executions);
  } else if (selectedNode === "discussion") {
    content = discussionMarkup(
      nodeExecutions("collaborate"),
      info.draft?.decisions ?? [],
    );
  } else if (selectedNode === "review") {
    content = executions
      .filter((x) => x.output)
      .map(
        (x) =>
          `<article class="record"><strong>${x.output.passed ? "模型审查通过" : "模型审查未通过"}</strong><p>${h(x.output.summary)}</p>${issues(x.output.issues)}<small>${h(x.id)}</small></article>`,
      )
      .join("");
    if (info.review)
      content +=
        "<h3>最终门禁（包含程序校验）</h3>" +
        issues(info.review.issues) +
        `<p>${info.risk_accepted ? "用户已明确接受风险，等待确认" : info.review.passed ? "可提交人工确认" : "尚未满足确认条件"}</p>` +
        (info.risk_accepted ? "<p>" + h(info.risk_accepted.note) + "</p>" : "");
  } else if (["generate", "confirm"].includes(selectedNode)) {
    const artifacts = viewed.artifacts.filter(
      (a) => a.stage_id === selectedStage,
    );
    const artifact = artifacts.at(-1);
    if (artifact)
      content = `<h3>${h(artifact.summary)}</h3><p>版本 ${artifact.version}${artifact.superseded ? " · 已失效" : ""}</p>${artifact.assumptions.length ? "<h3>设计假设</h3>" + list(artifact.assumptions) : ""}${specPreview(artifact.spec)}<details><summary>规格 JSON</summary>${code(artifact.spec)}</details><details><summary>与上一版的差异</summary>${code(artifact.diff.join("\n") || "无差异")}</details><h3>版本记录</h3>${artifacts.map((a) => `<details><summary>v${a.version} · ${h(a.created_at.slice(11, 19))}${a.superseded ? " · 已失效" : ""}</summary>${code(a.spec)}</details>`).join("")}`;
    if (selectedStage === "confirmation" && viewed.execution)
      content = `<p>校验与资源编排已完成。确认后可导出执行计划。</p>${code({ tasks: viewed.execution.nodes.length, batches: viewed.execution.batches.length, config: viewed.execution.config })}`;
  } else if (selectedNode === "validate" && latest?.output) {
    const result = latest.output;
    content = `<h3>${result.errors?.length ? "校验未通过" : "契约与依赖校验通过"}</h3>${list(result.errors)}<p>覆盖率、需求引用和依赖结构由原校验器检查。</p><h3>需求覆盖</h3>${code(result.coverage)}<details><summary>完整校验报告</summary>${code(result)}</details>`;
  } else if (selectedNode === "compile" && latest?.output) {
    const result = latest.output;
    content = `<h3>${result.compiled ? "编排成功" : "编排受阻"}</h3><p>${result.nodes?.length ?? 0} 个任务 · ${result.batches?.length ?? 0} 个资源批次</p><h3>并发与角色容量</h3>${code(result.config)}<h3>阻塞原因</h3>${result.blocked?.length ? code(result.blocked) : "<p>无阻塞项</p>"}<details><summary>完整编排结果</summary>${code(result)}</details>`;
  } else if (selectedNode === "plan" && planForView()) {
    content =
      "<h3>任务清单</h3>" +
      planForView()
        .plan.tasks.map(
          (task) =>
            `<button type="button" class="planning-task" data-task="${h(task.id)}" aria-pressed="${selectedTask === task.id}"><small>${h(task.id)} · ${h(roleName(task.role))}</small><strong>${h(task.title)}</strong><span>${h(task.description)}</span></button>`,
        )
        .join("");
    const tasks = planForView().plan.tasks;
    const task = tasks.find(t => t.id === selectedTask);
    content = `<div class="planning-layout ${task ? "has-detail" : ""}"><div>${content}</div>${task ? `<aside class="execution-detail"><header><strong>任务详情</strong><button type="button" data-close-task>关闭</button></header>${taskDetail(task, tasks, null)}</aside>` : ""}</div>`;
  } else if (selectedNode === "spec_adapter") {
    content = `<h3>任务规划依据</h3><p>根据已确认的设计规格拆解开发任务。</p>${specMarkup(viewed.spec, "architecture")}`;
  } else if (latest?.output) {
    content = code(latest.output);
  }
  if (latest?.error) content += `<div class="issue">${h(latest.error)}</div>`;
  if (!content)
    content =
      '<div class="empty"><strong>尚无执行记录</strong><p>阶段启动后，这里展示真实调用结果与产物。</p></div>';
  if (
    ["generate", "confirm"].includes(selectedNode) &&
    info.draft &&
    !replaying &&
    !["running", "pausing", "confirming"].includes(liveRun.status)
  )
    content =
      '<div class="editor-toolbar"><button data-edit-spec>编辑方案内容</button></div>' +
      content;
  $("details").innerHTML = content;
}
function specPreview(spec) {
  return specMarkup(spec, selectedStage);
}

function issues(items = []) {
  return items
    .map(
      (i) =>
        `<div class="issue ${i.severity === "warning" ? "warning" : ""}"><strong>${h(i.id)} · ${i.severity === "blocking" ? "阻断" : "提醒"}</strong><p>${h(i.message)}</p>${i.requires_user ? "<small>需要用户决定</small>" : ""}</div>`,
    )
    .join("");
}
function renderStageActions() {
  let html = "";
  const current = selectedStage === viewed.stage;
  const front = ["requirements", "product", "architecture"].includes(
    selectedStage,
  );
  if (current && viewed.status === "needs_input" && selectedNode === "confirm")
    html = `${questionMarkup(stageInfo())}${list(stageInfo().draft?.disagreements)}<details><summary>审查问题与背景</summary>${issues(stageInfo().review?.issues)}</details>`;
  if (
    !replaying &&
    front &&
    !["running", "pausing", "confirming"].includes(liveRun.status)
  ) {
    html += `<details ${current && viewed.status === "needs_input" ? "open" : ""}><summary>${current && viewed.status === "needs_input" ? "回答问题并重新审查" : "请求修订本阶段"}</summary><p class="muted">本阶段及下游结果将失效，历史记录保留。不会自动改变需求范围。</p><label for="answer">回答或修改原因</label><textarea id="answer" rows="3" maxlength="12000"></textarea><details><summary>直接编辑规格 JSON（可选）</summary><p class="muted">提交内容视为你明确提出的基线变更，仍需重新审查与确认。</p><textarea id="edited-spec" rows="8" aria-label="修改后的规格 JSON"></textarea></details><div class="actions"><button data-replan>提交并重新审查</button>${current && viewed.status === "needs_input" && !stageInfo().questions?.length && !stageInfo().review?.issues?.some((i) => i.requires_user) ? "<button data-accept-risk>明确接受当前风险</button>" : ""}</div></details>`;
  }
  if (
    viewed.status === "complete" &&
    viewed.plan_ref &&
    selectedStage === "confirmation"
  ) {
    const ref = viewed.plan_ref;
    html += `<div class="export-links"><a href="/plans/${encodeURIComponent(ref.plan_id)}/export?revision=${ref.revision}&format=json" download>导出计划 JSON</a><a href="/plans/${encodeURIComponent(ref.plan_id)}/export?revision=${ref.revision}&format=markdown" download>导出计划 Markdown</a></div>`;
  }

  const info = stageInfo();
  if (
    current &&
    !replaying &&
    front &&
    viewed.status === "needs_input" &&
    !info.questions?.length &&
    !info.review?.issues?.some(
      (item) => item.severity === "blocking" || item.requires_user,
    )
  )
    html =
      '<div class="issue warning"><p>没有列出待回答问题。可使用已保存的审查结果重新检查推进条件；不会调用模型、消耗答疑额度或代你确认。</p><button data-reassess>重新检查推进条件</button></div>' +
      html;
  if (info.discussion_notes?.length)
    html =
      `<details><summary>讨论保留事项与后续验证</summary><p class="muted">本轮审查未将这些事项判为阻断；保留记录，不代表已经完成技术验证。</p>${list(info.discussion_notes)}</details>` +
      html;
  if (info.ai_decisions?.length)
    html =
      `<section class="ai-decisions"><h3>${info.provisional ? "AI 暂定方案与影响" : "AI 自主处理记录"}</h3>${info.ai_decisions.map((item) => `<article class="discussion-decision"><strong>${h(item.question)}</strong><p>采用：${h(item.choice)}</p><p>依据：${h(item.reason)}</p><p class="muted">影响：${h(item.impact)}</p></article>`).join("")}</section>` +
      html;
  if (current && info.provisional) {
    html =
      '<div class="issue"><strong>存在 AI 暂定事项，尚不等于审查通过</strong><p>请核对暂定选择与影响。确认后才进入下一阶段；也可编辑方案或主动追加澄清。</p></div>' +
      html;
    if (!replaying)
      html += `<label class="provisional-ack"><input id="ack-provisional" type="checkbox" ${provisionalAcknowledged.has(ackKey()) ? "checked" : ""}>我已核对并接受 AI 暂定事项及其影响</label>`;
  }
  if (current && viewed.status === "needs_resolution")
    html =
      '<div class="issue"><strong>明确约束或程序校验仍冲突</strong><p>请编辑方案以保留明确要求，或主动追加一轮澄清。系统不会自行推翻约束。</p></div>' +
      issues(info.hard_conflicts) +
      html;
  if (
    current &&
    front &&
    !replaying &&
    ["needs_resolution", "needs_input", "awaiting_confirmation"].includes(
      viewed.status,
    )
  )
    html +=
      '<div class="editor-toolbar"><button data-extend>主动追加一轮澄清</button></div>';
  $("stage-actions").innerHTML = html;
  const documentPanel = document.querySelector(".stage-document");
  if (
    current &&
    selectedNode === "confirm" &&
    (["needs_input", "needs_resolution"].includes(viewed.status) ||
      info.provisional)
  )
    documentPanel.insertBefore($("stage-actions"), $("details"));
  else documentPanel.append($("stage-actions"));
}
function renderDag() {
  $("dag").hidden = true;
}
function renderResources() {
  if (selectedNode === "validate") {
    $("detail-title").textContent = "计划校验";
    $("details").innerHTML = `<h3>${viewed.validation?.errors?.length ? "计划校验未通过" : "计划校验通过"}</h3><p>检查任务字段、需求覆盖与依赖结构。</p>${list(viewed.validation?.errors)}<details><summary>查看完整校验报告</summary>${code(viewed.validation)}</details>`;
    return;
  }
  const execution = viewed.execution;
  if (!execution) {
    $("details").innerHTML = "<p>任务计划正在校验，完成后可设置执行资源。</p>";
    return;
  }
  if (selectedNode === "confirm") {
    const config = execution.config ?? {};
    const blocked = execution.compiled === false || resourceDirty() || viewed.validation?.errors?.length;
    $("detail-title").textContent = "核对并确认执行计划";
    const roles = Object.entries(config.role_capacity ?? {});
    $("details").innerHTML = `<section class="final-plan"><div class="confirmation-intro"><h3>${viewed.status === "complete" ? "计划已确认" : "开发前的最后一次核对"}</h3><p>确认任务范围、执行资源和批次安排。确认后先展示工作包与任务图谱，由你点击「开始执行」才启动开发。</p></div><div class="final-plan-grid"><section><h3>计划范围</h3><dl><dt>开发任务</dt><dd>${execution.nodes.length} 项</dd><dt>任务依赖</dt><dd>${execution.edges.length} 条</dd><dt>执行批次</dt><dd>${execution.batches.length} 批</dd><dt>计划修订</dt><dd>r${h(viewed.plan_ref?.revision)}</dd></dl></section><section><h3>已保存的资源配置</h3><dl><dt>总并发上限</dt><dd>${h(config.max_concurrency)}</dd>${roles.map(([role,capacity]) => `<dt>${h(roleName(role))}容量</dt><dd>${h(capacity)}</dd>`).join("")}</dl><button type="button" data-open-resources>返回资源配置</button></section></div><div class="confirmation-checks"><p>${viewed.validation?.errors?.length ? "计划校验仍有问题" : "任务与依赖校验通过"}</p><p>${execution.compiled === false ? "资源编排受阻，请调整容量" : "执行批次已计算"}</p>${resourceDirty() ? '<p class="issue">资源配置尚未保存，请先返回资源配置重新计算。</p>' : ""}</div>${viewed.status === "complete" ? '<button type="button" data-open-execution class="primary">查看工作包与任务图谱 →</button>' : `<button type="button" data-confirm-plan class="primary" ${blocked || busy || replaying || viewed.status !== "awaiting_confirmation" ? "disabled" : ""}>确认计划，进入编排执行</button>`}</section>`;
    return;
  }
  const locked = replaying || viewed.status !== "awaiting_confirmation" || busy;
  const key = resourceKey();
  if (!resourceDraft || resourceDraft.key !== key || !resourceDraft.dirty) {
    resourceDraft = { key, config: structuredClone(execution.config ?? {max_concurrency: 2, role_capacity: {}}), dirty: false };
  }
  const config = resourceDraft.config;
  const roles = [...new Set((planForView()?.plan?.tasks ?? []).map(t => t.role))];
  const disable = locked ? "disabled" : "";
  $("detail-title").textContent = "资源配置与排程";
  $("details").innerHTML = `<div class="confirmation-intro"><h3>安排执行资源</h3><p>设置同时执行的任务数与各角色容量，重新计算批次后确认。确认后进入编排执行，由你手动开始开发。</p></div><form id="resource-form"><fieldset ${disable}><legend>并发配置</legend><div class="resource-fields"><label>总并发上限<input name="max_concurrency" type="number" min="1" max="100" step="1" required value="${h(config.max_concurrency ?? 2)}"><small>所有角色同时执行的任务总数</small></label>${roles.map(role => `<label>${h(roleName(role))}容量<input data-role-capacity="${h(role)}" type="number" min="0" max="100" step="1" required value="${h(config.role_capacity?.[role] ?? config.max_concurrency ?? 2)}"><small>该角色可同时执行的任务数</small></label>`).join("")}</div><div class="resource-footer"><span id="resource-state" role="status">${resourceDraft.dirty ? "配置已修改，请重新计算批次。" : "当前配置已计算执行批次。"}</span><button type="submit">重新计算批次</button></div></fieldset></form><section class="compile-overview"><h3>${execution.compiled === false ? "资源编排受阻" : "执行安排"}</h3><p>${execution.nodes.length} 个任务 · ${execution.edges.length} 条依赖 · ${execution.batches.length} 个批次</p>${execution.blocked?.length ? code(execution.blocked) : ""}<details><summary>查看批次任务清单</summary>${execution.batches.map((ids,i) => `<p>批次 ${i+1}：${ids.map(id => h(planForView()?.plan?.tasks.find(t => t.id === id)?.title ?? id)).join("、")}</p>`).join("")}</details><details><summary>校验与资源等待记录</summary>${code({validation: viewed.validation, batch_details: execution.batch_details})}</details></section>${viewed.status === "complete" ? '<button type="button" data-open-execution class="primary">查看工作包与任务图谱 →</button>' : ''}`;
}
function renderEvents() {
  const all = events.filter((e) => e.sequence <= viewed.sequence);
  const query = $("event-search").value.trim().toLowerCase();
  const visible = all.filter(
    (event) =>
      !query ||
      [
        eventLabels[event.type] ?? event.type,
        stageLabel(event.stage_id),
        nodeLabels[event.node_id] ?? event.node_id,
        event.reason,
        event.note,
        event.sequence,
      ]
        .filter((value) => value !== undefined)
        .join(" ")
        .toLowerCase()
        .includes(query),
  );
  $("event-count").textContent = String(all.length);
  $("event-shown").textContent =
    `显示 ${Math.min(visible.length, 200)} / ${all.length} 条`;
  $("events").innerHTML = visible
    .slice(-200)
    .reverse()
    .map(
      (e) =>
        `<li><time>${h(new Date(e.timestamp).toLocaleTimeString("zh-CN", { hour12: false }))}</time><span>${h(eventLabels[e.type] ?? e.type)}</span><span>${h(e.reason ?? e.note ?? (e.node_id ? nodeLabels[e.node_id] : e.passed === undefined ? stageLabel(e.stage_id) : e.passed ? "审查通过" : "审查未通过"))}</span></li>`,
    )
    .join("");
  $("replay").max = String(liveRun.sequence);
  $("replay").value = String(viewed.sequence);
  $("replay-caption").textContent = replaying
    ? `历史只读 · 事件 ${viewed.sequence} / ${liveRun.sequence} · 操作不会改变后台运行`
    : "实时观察 · " + liveRun.sequence + " 条事件";
}
async function loadRun(id, { initial = false } = {}) {
  const state = await api("/studio/runs/" + encodeURIComponent(id));
  if (rid !== id) return;
  const changed = liveRun?.sequence !== state.sequence;
  liveRun = state;
  if (initial || changed) {
    let batch;
    do {
      batch = await api(
        "/studio/runs/" +
          encodeURIComponent(id) +
          "/events?after=" +
          (events.at(-1)?.sequence ?? 0),
      );
      events.push(...batch);
    } while (batch.length === 1000);
    if (state.plan_ref) {
      plan = await api(
        "/plans/" +
          encodeURIComponent(state.plan_ref.plan_id) +
          "?revision=" +
          state.plan_ref.revision,
      );
      plans.set(state.plan_ref.plan_id + ":" + state.plan_ref.revision, plan);
    } else plan = null;
    if (rid !== id) return;
    if (!replaying) {
      viewed = state;
      const destination = state.status === "complete" ? "execution" : state.stage;
      if (initial || (follow && selectedStage !== destination)) {
        selectedStage = destination;
        selectedTask = null;
        selectedNode = preferredNode(selectedStage);
      }
      render();
    }
  }
}
async function openRun(id) {
  editing = false;
  editBase = null;
  clearTimeout(timer);
  replayRequest++;
  rid = id;
  replaying = false;
  liveRun = null;
  events = [];
  selectedTask = null;
  const url = new URL(location.href);
  url.searchParams.set("run", id);
  history.replaceState(null, "", url);
  await loadRun(id, { initial: true });
  poll();
}
function poll() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    try {
      if (rid) await loadRun(rid);
    } catch (e) {
      notice(e.message);
    }
    if (rid) poll();
  }, 1200);
}
async function command(action, extra = {}) {
  if (busy || replaying) return;
  if (action === "confirm" && liveRun.stage === "confirmation" && resourceDirty()) { notice("请先重新计算批次，再确认执行计划。"); return; }
  busy = true;
  render();
  notice();
  try {
    await api("/studio/runs/" + encodeURIComponent(rid) + "/commands", {
      action,
      expected_sequence: liveRun.sequence,
      ...extra,
    });
    if (action === "compile") resourceDraft = null;
    await loadRun(rid);
    if (action === "confirm" && liveRun.status === "complete") {
      selectedStage = "execution";
      selectedTask = null;
      selectedNode = preferredNode(selectedStage);
    }
    await refreshRuns();
  } catch (e) {
    notice(e.message);
    await loadRun(rid).catch(() => {});
  } finally {
    busy = false;
    render();
  }
}
async function refreshRuns() {
  const runs = await api("/studio/runs");
  $("runs").innerHTML =
    '<option value="">选择运行</option>' +
    runs
      .map(
        (r) =>
          `<option value="${h(r.id)}" ${r.id === rid ? "selected" : ""}>${h(r.product)} · ${h(labels[r.status])}</option>`,
      )
      .join("");
}
$("intake").onsubmit = async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  const button = event.submitter;
  button.disabled = true;
  notice();
  try {
    const run = await api("/studio/runs", {
      source: $("source").value,
      input_mode: $("input-mode").value,
    });
    await openRun(run.id);
    await refreshRuns();
  } catch (e) {
    notice(e.message);
  } finally {
    busy = false;
    button.disabled = false;
    if (viewed) render();
  }
};
$("file").onchange = async () => {
  const file = $("file").files[0];
  if (!file) return;
  if (file.size > 240000) {
    notice("文件过大，请使用 60,000 字符以内的文本。");
    return;
  }
  $("source").value = await file.text();
};
$("runs").onchange = () => {
  if ($("runs").value) openRun($("runs").value).catch((e) => notice(e.message));
};
$("new-run").onclick = () => {
  $("run-failure").hidden = true;
  setEventsOpen(false);
  $("toggle-events").disabled = true;
  $("event-count").textContent = "0";
  editing = false;
  editBase = null;
  clearTimeout(timer);
  replayRequest++;
  rid = null;
  liveRun = null;
  viewed = null;
  replaying = false;
  history.replaceState(null, "", location.pathname);
  $("workspace").hidden = true;
  $("intake").hidden = false;
  $("project-name").textContent = "创建项目工作流";
  $("run-caption").textContent = "从一个需求开始，让每一步设计都有依据。";
  renderIdleStages();
  document.querySelector(".sidebar").classList.remove("open");
  $("toggle-stages").setAttribute("aria-expanded", "false");
  notice();
};
$("stages").onclick = (e) => {
  const button = e.target.closest("[data-stage]");
  if (!button) return;
  follow = false;
  $("follow").checked = false;
  document.querySelector(".sidebar").classList.remove("open");
  $("toggle-stages").setAttribute("aria-expanded", "false");
  editing = false;
  editBase = null;
  selectedStage = button.dataset.stage;
  selectedNode = preferredNode(selectedStage);
  selectedTask = null;
  render();
  document.querySelector(".stage-document").scrollTop = 0;
};
function selectContent(e) {
  const button = e.target.closest("[data-node]");
  if (button) {
    editing = false;
    editBase = null;
    selectedNode = button.dataset.node;
    selectedTask = null;
    render();
    document.querySelector(".stage-document").scrollTop = 0;
  }
}
$("canvas").onclick = selectContent;
$("stage-steps").onclick = selectContent;
$("dag").onclick = (e) => {
  const button = e.target.closest("[data-task]");
  if (button) {
    selectedTask = button.dataset.task;
    renderDetail();
  }
};
$("controls").onclick = (e) => {
  if (e.target.closest("[data-open-final]")) { selectedStage = "confirmation"; selectedNode = "confirm"; selectedTask = null; render(); return; }
  const button = e.target.closest("[data-command]");
  if (button)
    command(
      button.dataset.command,
      button.dataset.command === "confirm"
        ? { acknowledge_provisional: provisionalAcknowledged.has(ackKey()) }
        : {},
    );
};
function ackKey() {
  const info = viewed.stages[viewed.stage] ?? {};
  return `${viewed.id}:${viewed.stage}:${info.epoch ?? 1}`;
}
$("stage-actions").addEventListener("change", (event) => {
  if (event.target.id === "ack-provisional") {
    if (event.target.checked) provisionalAcknowledged.add(ackKey());
    else provisionalAcknowledged.delete(ackKey());
    render();
  }
});
$("stage-actions").addEventListener("click", (event) => {
  if (event.target.closest("[data-reassess]")) command("reassess");
  if (event.target.closest("[data-extend]"))
    command("extend_clarification", {
      note: "用户主动追加一轮澄清",
      target_stage: selectedStage,
    });
});
$("stage-actions").onclick = (e) => {
  const accept = e.target.closest("[data-accept-risk]");
  if (!accept && !e.target.closest("[data-replan]")) return;
  const note = $("answer").value;
  if (accept) {
    const risks = (stageInfo().review?.issues || [])
      .map((issue) => issue.message)
      .filter(Boolean);
    command("accept_risk", {
      note:
        note.trim() ||
        `用户点击“明确接受当前风险”，接受本次审查列出的风险：${risks.join("；") || "当前审查风险"}`,
    });
    return;
  }
  if (!note.trim()) {
    notice("请填写回答或修改原因。");
    return;
  }
  let spec;
  try {
    if ($("edited-spec").value.trim())
      spec = JSON.parse($("edited-spec").value);
  } catch {
    notice("修改后的规格不是有效 JSON。");
    return;
  }
  command(liveRun.status === "needs_input" ? "answer" : "replan", {
    note,
    target_stage: selectedStage,
    ...(spec ? { spec } : {}),
  });
};
$("stage-alert").onclick = (event) => {
  if (event.target.closest("[data-open-confirm]")) {
    selectedNode = "confirm";
    editing = false;
    render();
    document.querySelector(".stage-document").scrollTop = 0;
  }
};
$("toggle-flow").onclick = () => {
  const expanded = $("flow-structure").hidden;
  $("flow-structure").hidden = !expanded;
  $("toggle-flow").setAttribute("aria-expanded", String(expanded));
  $("toggle-flow").textContent = expanded ? "收起执行结构" : "查看执行结构";
  renderGraph();
};
$("details").onclick = async (event) => {
  if (event.target.closest("[data-confirm-plan]")) { command("confirm"); return; }
  if (event.target.closest("[data-open-resources]")) { selectedNode = "compile"; render(); return; }
  const taskButton = event.target.closest("[data-task]");
  if (taskButton && selectedStage === "planning") {
    selectedTask = taskButton.dataset.task;
    renderDetail();
  }
  if (event.target.closest("[data-close-task]")) { selectedTask = null; renderDetail(); }
  if (event.target.closest("[data-open-execution]")) {
    selectedStage = "execution";
    selectedTask = null;
    render();
  }
  if (event.target.closest("[data-edit-spec]")) {
    editBase = structuredClone(stageInfo().draft.spec);
    editing = true;
    renderDetail();
    renderDag();
  }
  if (event.target.closest("[data-cancel-edit]")) {
    editing = false;
    editBase = null;
    renderDetail();
    renderDag();
  }
  if (event.target.closest("[data-submit-edit]")) {
    const spec = collectSpec($("details"), editBase),
      note =
        $("edit-note").value.trim() ||
        "用户直接编辑了本阶段方案，请整合修改并重新审查。";
    editing = false;
    editBase = null;
    await command("replan", { note, target_stage: selectedStage, spec });
  }
};
$("details").addEventListener("input", event => {
  if (!event.target.closest("#resource-form") || replaying || !resourceDraft) return;
  const form = $("resource-form");
  resourceDraft.config.max_concurrency = Number(form.elements.max_concurrency.value);
  resourceDraft.config.role_capacity = Object.fromEntries([...form.querySelectorAll("[data-role-capacity]")].map(input => [input.dataset.roleCapacity, Number(input.value)]));
  resourceDraft.dirty = true;
  $("resource-state").textContent = "配置已修改，请重新计算批次。";
  $("controls").querySelectorAll('[data-command="confirm"]').forEach(b => { b.disabled = true; });
});
$("details").addEventListener("submit", event => {
  if (event.target.id !== "resource-form") return;
  event.preventDefault();
  if (event.target.reportValidity() && !replaying && !busy)
    command("compile", { config: resourceDraft.config });
});
$("stage-actions").addEventListener("click", async (event) => {
  const option = event.target.closest("[data-answer-option]");
  if (option) {
    const question = option.closest("[data-question]");
    for (const item of question.querySelectorAll("[data-answer-option]"))
      item.setAttribute("aria-pressed", String(item === option));
  }
  if (event.target.closest("[data-submit-answers]")) {
    const answers = [...$("stage-actions").querySelectorAll("[data-question]")]
      .map((question) => ({
        question_id: question.dataset.question,
        selected:
          question.querySelector('[data-answer-option][aria-pressed="true"]')
            ?.dataset.answerLabel ?? "",
        free_text: question.querySelector("[data-answer-free]").value.trim(),
      }))
      .filter((answer) => answer.selected || answer.free_text);
    if (!answers.length) {
      notice("请选择一项建议或输入自己的回答。");
      return;
    }
    const note = answers
      .map(
        (answer) =>
          `${answer.question_id}：${answer.selected}${answer.free_text ? "；" + answer.free_text : ""}`,
      )
      .join("\n");
    await command("answer", { note, target_stage: selectedStage, answers });
  }
});
$("follow").onchange = () => {
  follow = $("follow").checked;
  if (follow) {
    selectedStage = viewed.stage;
    selectedNode = preferredNode(selectedStage);
    selectedTask = null;
    render();
  }
};
$("show-feedback").onchange = () => {
  feedbackVisible = $("show-feedback").checked;
  renderGraph();
  renderGlobalFeedback();
};
let replayRequest = 0;
$("replay").oninput = async () => {
  editing = false;
  editBase = null;
  const request = ++replayRequest;
  replaying = true;
  try {
    const snapshot = await api(
      "/studio/runs/" +
        encodeURIComponent(rid) +
        "?sequence=" +
        $("replay").value,
    );
    if (request !== replayRequest) return;
    if (snapshot.plan_ref) {
      const ref = snapshot.plan_ref;
      plans.set(
        ref.plan_id + ":" + ref.revision,
        await api(
          "/plans/" +
            encodeURIComponent(ref.plan_id) +
            "?revision=" +
            ref.revision,
        ),
      );
    }
    if (request !== replayRequest) return;
    viewed = snapshot;
    selectedTask = null;
    render();
  } catch (e) {
    notice(e.message);
  }
};
$("live").onclick = () => {
  replayRequest++;
  replaying = false;
  viewed = liveRun;
  render();
};
$("export-trace").onclick = () =>
  download(
    (rid ?? "workflow") + "-trace.json",
    pretty({ run: liveRun, events }),
  );
function renderIdleStages() {
  $("stages").innerHTML = definition.stages
    .map(
      (stage, index) =>
        `<button type="button" class="stage" disabled><i class="stage-symbol" aria-hidden="true">${index + 1}</i><strong>${h(stage.label)}</strong></button>`,
    )
    .join("");
  $("global-feedback").hidden = true;
}
$("toggle-stages").onclick = () => {
  const open = document.querySelector(".sidebar").classList.toggle("open");
  $("toggle-stages").setAttribute("aria-expanded", String(open));
};
document.addEventListener("click", (event) => {
  if (
    !event.target.closest(".sidebar") &&
    !event.target.closest("#toggle-stages")
  ) {
    document.querySelector(".sidebar").classList.remove("open");
    $("toggle-stages").setAttribute("aria-expanded", "false");
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    document.querySelector(".sidebar").classList.remove("open");
    $("toggle-stages").setAttribute("aria-expanded", "false");
  }
});
function setEventsOpen(open) {
  $("runtime-events").hidden = !open;
  document.querySelector(".app-shell").classList.toggle("events-open", open);
  $("toggle-events").setAttribute("aria-expanded", String(open));
  if (
    !open &&
    document.activeElement &&
    $("runtime-events").contains(document.activeElement)
  )
    $("toggle-events").focus();
}
$("toggle-events").onclick = () => setEventsOpen($("runtime-events").hidden);
$("toggle-log").onclick = () => setEventsOpen(false);
$("event-search").oninput = () => {
  if (viewed) renderEvents();
};
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("runtime-events").hidden)
    setEventsOpen(false);
});

try {
  [definition] = await Promise.all([api("/studio/definition"), refreshRuns()]);
  for (const flow of Object.values(definition.subworkflows)) {
    for (const node of flow.nodes) if (nodeLabels[node.id]) node.label = nodeLabels[node.id];
  }
  renderIdleStages();
  const health = await api("/health");
  $("connection").textContent = health.live_llm_configured
    ? "模型服务已配置 · 启动后调用真实 API"
    : "模型尚未配置 · 可先创建运行";
  if (rid) await openRun(rid);
} catch (e) {
  notice("无法连接工作流服务：" + e.message);
  $("connection").textContent = "请通过 Python 后端打开本页";
}

let graphWidth = 0;
new ResizeObserver((entries) => {
  const width = Math.round(entries[0].contentRect.width);
  if (width !== graphWidth) {
    graphWidth = width;
    if (viewed && !$("flow-structure").hidden) renderGraph();
  }
}).observe($("canvas"));
