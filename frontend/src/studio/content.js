import { escapeHtml as h } from "../shared/dom.js";

const lines = (value) =>
  Array.isArray(value) ? value.join("\n") : String(value ?? "");
const paragraph = (value) => `<p>${h(value)}</p>`;
export function proposalsMarkup(executions) {
  const groups = new Map();
  for (const execution of executions.filter(
    (item) => item.role && item.output,
  )) {
    const role = execution.role.id;
    if (!groups.has(role)) groups.set(role, []);
    groups.get(role).push(execution);
  }
  if (!groups.size)
    return '<div class="empty"><strong>等待角色出案</strong><p>启动本阶段后，各角色分别提出职责范围内的设计。</p></div>';
  return `<div class="proposal-grid">${[...groups.values()]
    .map((history) => {
      const current = history.at(-1),
        output = current.output;
      return `<article class="proposal-document"><div class="record-header"><h3>${h(current.role.name)}</h3><span class="tag">${history.length} 次出案 / 评议</span></div><p class="proposal-context">${h(current.role.focus)}</p><p>${h(output.summary)}</p>${output.sections?.length ? output.sections.map((section) => `<h4>${h(section.title)}</h4>${paragraph(section.content)}${section.requirement_ids?.length ? `<small>关联需求：${h(section.requirement_ids.join("、"))}</small>` : ""}`).join("") : '<p class="proposal-context">历史记录仅包含意见摘要，未生成新版结构化方案。</p>'}${output.risks?.length ? "<h4>风险与边界</h4>" + output.risks.map(paragraph).join("") : ""}<details><summary>查看首次提案与执行记录</summary>${history.map((item) => `<h4>${h(item.id)}</h4>${paragraph(item.output.summary)}${(item.output.sections ?? []).map((section) => `<strong>${h(section.title)}</strong>${paragraph(section.content)}`).join("")}<small>${h(item.latency_ms ?? "—")} ms · 尝试 ${item.attempt}</small>`).join("")}</details></article>`;
    })
    .join("")}</div>`;
}
export function discussionMarkup(executions, decisions = []) {
  const proposals = executions.filter((item) => item.role && item.output);
  const lookup = new Map(proposals.map((item) => [item.key, item]));
  const replies = proposals.flatMap((item) =>
    (item.output.replies ?? []).map((reply) => ({ item, reply })),
  );
  const positions = { agree: "同意", object: "反对", supplement: "补充" };
  return `<h3>相互评议</h3>${replies.length ? replies.map(({ item, reply }) => `<article class="discussion-decision"><strong>${h(item.role.name)} · ${positions[reply.position] ?? h(reply.position)} ${h(lookup.get(reply.proposal_id)?.role.name ?? reply.proposal_id)}</strong><p>${h(reply.reason)}</p>${reply.change ? `<p>方案调整：${h(reply.change)}</p>` : ""}</article>`).join("") : '<p class="muted">此运行尚无带引用的相互评议记录。旧记录不补造讨论。</p>'}<h3>主持采纳记录</h3>${decisions.length ? decisions.map((decision) => `<article class="discussion-decision"><span class="tag">${{ accepted: "采纳", rejected: "拒绝", needs_evidence: "待补证据" }[decision.disposition]}</span><strong> ${h(lookup.get(decision.proposal_id)?.role.name ?? decision.proposal_id)}</strong><p>${h(decision.reason)}</p><small>${h(decision.proposal_id)}</small></article>`).join("") : '<p class="muted">主持尚未形成决策。</p>'}`;
}
export function specMarkup(spec, stage) {
  const section = (title, body) =>
    `<section class="spec-section"><h3>${title}</h3>${body || '<p class="muted">尚未确定</p>'}</section>`;
  const rows = (items, render) =>
    (items ?? [])
      .map(
        (item) =>
          `<article class="spec-row"><span>${h(item.id)}</span><div>${render(item)}</div></article>`,
      )
      .join("");
  let output = `<div class="readable-spec"><h3>${h(spec.product)}</h3>${paragraph(spec.scope?.goal)}`;
  if (stage === "requirements") {
    output += section(
      "包含范围",
      (spec.scope?.included ?? []).map(paragraph).join(""),
    );
    output += section(
      "明确排除",
      (spec.scope?.excluded ?? []).map(paragraph).join(""),
    );
  }
  output += section(
    "需求与验收",
    rows(
      spec.requirements,
      (item) =>
        `<strong>${h(item.goal)}</strong>${paragraph(lines(item.acceptance))}`,
    ),
  );
  if (stage !== "requirements")
    output += section(
      "模块职责",
      rows(
        spec.modules,
        (item) =>
          paragraph(item.responsibility) +
          `<small>关联需求：${h((item.requirement_ids ?? []).join("、"))}</small>`,
      ),
    );
  if (stage !== "requirements")
    output += section(
      stage === "architecture" ? "接口、数据与系统设计" : "页面与业务设计",
      rows(
        spec.design_items,
        (item) =>
          `<span class="tag">${h(item.kind)}</span>${paragraph(item.description)}<small>关联需求：${h((item.requirement_ids ?? []).join("、"))}</small>`,
      ),
    );
  output += section(
    "约束",
    rows(spec.constraints, (item) => paragraph(item.detail)),
  );
  return output + "</div>";
}
const field = (label, path, value, list = false) =>
  `<label>${h(label)}<textarea data-spec-path="${h(path)}" data-list="${list}" rows="${list ? 3 : 2}">${h(lines(value))}</textarea></label>`;
export function editorMarkup(spec, stage) {
  let content = "";
  if (stage === "requirements") {
    content +=
      field("项目名称", "product", spec.product) +
      field("项目目标", "scope.goal", spec.scope?.goal) +
      field(
        "包含范围（每行一项）",
        "scope.included",
        spec.scope?.included,
        true,
      ) +
      field(
        "明确排除（每行一项）",
        "scope.excluded",
        spec.scope?.excluded,
        true,
      );
    content += (spec.requirements ?? [])
      .map(
        (item, index) =>
          `<h3>${h(item.id)}</h3>${field("需求行为", `requirements.${index}.goal`, item.goal)}${field("验收条件", `requirements.${index}.acceptance`, item.acceptance, Array.isArray(item.acceptance))}`,
      )
      .join("");
  } else {
    if (stage === "product")
      content += (spec.modules ?? [])
        .map(
          (item, index) =>
            `<h3>${h(item.id)}</h3>${field("模块职责", `modules.${index}.responsibility`, item.responsibility)}`,
        )
        .join("");
    content += (spec.design_items ?? [])
      .map(
        (item, index) =>
          `<h3>${h(item.id)} · ${h(item.kind)}</h3>${field("设计内容", `design_items.${index}.description`, item.description)}`,
      )
      .join("");
  }
  content += (spec.constraints ?? [])
    .map(
      (item, index) =>
        `<h3>${h(item.id)}</h3>${field("约束内容", `constraints.${index}.detail`, item.detail)}`,
    )
    .join("");
  return `<div class="spec-editor"><p class="muted">直接修改可读内容。提交后主持整合并重新审查，不会直接确认。</p>${content}<label>修改说明<textarea id="edit-note" rows="2" placeholder="说明你希望调整的内容及原因"></textarea></label><div class="editor-toolbar"><button class="primary" data-submit-edit>提交修改并复查</button><button data-cancel-edit>取消</button></div></div>`;
}
export function collectSpec(root, base) {
  const result = structuredClone(base);
  for (const element of root.querySelectorAll("[data-spec-path]")) {
    const path = element.dataset.specPath.split(".");
    let parent = result;
    for (let index = 0; index < path.length - 1; index++) {
      const key = path[index];
      parent = parent[key] ??= /^\d+$/.test(path[index + 1]) ? [] : {};
    }
    parent[path.at(-1)] =
      element.dataset.list === "true"
        ? element.value
            .split("\n")
            .map((value) => value.trim())
            .filter(Boolean)
        : element.value;
  }
  return result;
}
export function questionMarkup(info) {
  const requests = info.draft?.decision_requests?.length
    ? info.draft.decision_requests.filter(
        (request) =>
          !info.questions || info.questions.includes(request.question),
      )
    : (info.questions ?? []).map((question, index) => ({
        id: "Q" + (index + 1),
        question,
        options: [],
      }));
  if (!requests.length) return "";
  return `<section class="decision-questions"><h3>需要你决定</h3><p class="muted">可以选择建议，也可以补充自己的回答；建议不会自动生效。</p>${requests.map((request) => `<article class="decision-question" data-question="${h(request.id)}"><strong>${h(request.question)}</strong>${request.recommendation ? `<p>AI 建议：${h(request.recommendation)}</p>` : ""}${request.impact ? `<p class="muted">影响：${h(request.impact)}</p>` : ""}<div class="decision-options">${(request.options ?? []).map((option, index) => `<button type="button" data-answer-option="${index}" data-answer-label="${h(option.label)}" aria-pressed="false" title="${h(option.description)}">${h(option.label)}</button>`).join("")}</div><textarea data-answer-free rows="2" aria-label="${h(request.question)}" placeholder="也可以输入自己的选择、约束或补充说明"></textarea></article>`).join("")}<button class="primary" data-submit-answers>提交回答并继续</button></section>`;
}
