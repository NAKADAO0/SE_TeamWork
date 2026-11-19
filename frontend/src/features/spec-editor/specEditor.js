import { splitSpecText, joinSpecText } from "../../shared/specText.js";
import { escapeHtml } from "../../shared/dom.js";

function describeSnapshot(draft) {
  const line = (title, value) => value ? `${title}：${value}` : "";
  const list = (items) => Array.isArray(items) ? items.map((item) => `• ${item}`).join("\n") : "";
  const scope = draft.scope ?? {};
  const product = typeof draft.product === "string" ? draft.product : draft.product?.name;
  const requirements = Array.isArray(draft.requirements) ? draft.requirements : [];
  return {
    source: [line("项目", product), line("目标", scope.goal)].filter(Boolean).join("\n"),
    scope: [scope.included?.length && `纳入范围\n${list(scope.included)}`,
      scope.excluded?.length && `明确排除\n${list(scope.excluded)}`].filter(Boolean).join("\n\n"),
    remaining: requirements.map((entry) => [
      [entry.id, entry.goal ?? entry.title].filter(Boolean).join(" · "),
      line("验收", Array.isArray(entry.acceptance) ? entry.acceptance.join("；") : entry.acceptance),
    ].filter(Boolean).join("\n")).join("\n\n"),
  };
}

/**
 * 规格与配置视图（成员 1）。
 * 支持 JSON / Markdown 设计规格输入、文件导入、缺口检查、确认锁定、模板与生成模式配置。
 */
export function mountSpecEditor(root, { store, actions }) {
  root.innerHTML = `
    <h2>产品设计规格</h2>
    <div class="ws-row">
      <div class="studio-samples" data-role="sample-menu" hidden>
        <div class="studio-sample-options">
          <button type="button" data-action="skip-sample">不载入样例</button>
          <button type="button" data-action="load-sample" data-sample="chat">聊天 MVP</button>
          <button type="button" data-action="load-sample" data-sample="community">内容社区 MVP</button>
          <button type="button" data-action="load-sample" data-sample="unseen">校园失物招领 MVP</button>
        </div>
      </div>
      <button type="button" data-action="import-file">导入文件</button>
      <input type="file" data-role="file" accept=".json,.md,.markdown,.txt" hidden>
    </div>
    <div class="spec-input-section">
      <div class="spec-input-heading">
        <label for="spec-source">自然语言需求</label>
        <button type="button" data-action="extract" class="primary">AI_Review · 生成范围</button>
      </div>
      <textarea id="spec-source" data-role="source" spellcheck="false"
        placeholder="描述你想做的产品、功能和限制；输入 / 可选择样例。"></textarea>
    </div>
    <div class="spec-input-section">
      <div class="spec-input-heading">
        <label for="spec-scope">产品范围与明确排除</label>
        <button type="button" data-action="confirm-scope">确认范围并生成规格</button>
      </div>
      <textarea id="spec-scope" data-role="scope" spellcheck="false"
        placeholder="第一次整理的结果显示在这里，可直接修改。"></textarea>
    </div>
    <div class="spec-input-section">
      <label for="spec-remaining">其余设计规格</label>
      <textarea id="spec-remaining" data-role="text" spellcheck="false"
        placeholder="确认范围后生成需求条目、模块、设计条目和约束，可直接修改。"></textarea>
    </div>
    <div class="ws-row">
      <button type="button" data-action="extract" class="primary">AI_Review</button>
      <button type="button" data-action="review-full" class="primary">AI_Review · 完整规格</button>
      <button type="button" data-action="check">检查缺口</button>
      <button type="button" data-action="confirm">人工 Review · 确认规格</button>
      <button type="button" data-action="reopen" hidden>修改</button>
      <button type="button" data-action="reopen-scope" hidden>修改范围</button>
      <button type="button" data-action="plan" class="primary">生成计划</button>
    </div>
    <section class="spec-review-report" data-role="spec-review" hidden aria-label="规格 AI_Review 报告"></section>
    <div data-role="issues" class="ws-issues"></div>
    <p data-role="ref" class="ws-meta"></p>
    <details class="studio-settings">
    <summary>生成设置</summary>
    <div class="ws-row">
      <label class="grow">模板 <select data-role="template"></select></label>
    </div>
    <p class="ws-meta" data-role="template-description"></p>
    <p class="ws-meta">生成方式：实时 PM Agent（LLM）</p>
    </details>
  `;

  const samples = root.querySelector('[data-role="sample-menu"]');
  const fileInput = root.querySelector('[data-role="file"]');
  const source = root.querySelector('[data-role="source"]');
  const scope = root.querySelector('[data-role="scope"]');
  const text = root.querySelector('[data-role="text"]');
  const issues = root.querySelector('[data-role="issues"]');
  const ref = root.querySelector('[data-role="ref"]');
  const template = root.querySelector('[data-role="template"]');
  const button = (name) => root.querySelector(`[data-action="${name}"]`);

  let templates = [];

  root.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target || target.disabled) return;
    const action = target.dataset.action;
    if (action === "skip-sample" || action === "load-sample") {
      samples.hidden = true;
      if (action === "load-sample") {
        const state = store.getState();
        if (state.busy || state.spec?.locked) return;
        actions.loadSample(target.dataset.sample);
      }
      source.focus();
    }
    if (action === "import-file") fileInput.click();
    if (action === "extract") actions.extractSpec();
    if (action === "review-full") actions.extractSpec("full");
    if (action === "confirm-scope") actions.generateRemainingSpec();
    if (action === "reopen-scope") actions.reopenScope();
    if (action === "check") actions.checkSpec();
    if (action === "confirm") actions.confirmSpec();
    if (action === "reopen") store.dispatch({ type: "SPEC_REOPEN" });
    if (action === "plan") actions.startPlanning();
  });

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    fileInput.value = "";
    if (!file) return;
    const content = await file.text();
    actions.importSpecText(content, file.name);
  });

  source.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !samples.hidden) {
      samples.hidden = true;
      return;
    }
    if (event.key === "/" && !event.isComposing && !event.ctrlKey && !event.metaKey &&
        !source.readOnly && !store.getState().busy && !source.value.trim()) {
      event.preventDefault();
      samples.hidden = false;
      samples.querySelector("button").focus();
    }
  });
  samples.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !samples.hidden) {
      samples.hidden = true;
      source.focus();
    }
  });

  source.addEventListener("input", () => {
    store.dispatch({ type: "SOURCE_TEXT_CHANGE", text: source.value });
  });
  scope.addEventListener("input", () => {
    store.dispatch({ type: "SCOPE_TEXT_CHANGE", text: joinSpecText(scope.value, text.value), parts: { scope: scope.value, remaining: text.value } });
  });
  text.addEventListener("input", () => {
    store.dispatch({ type: "SPEC_TEXT_CHANGE", text: joinSpecText(scope.value, text.value), parts: { scope: scope.value, remaining: text.value } });
  });

  template.addEventListener("change", () => actions.setTemplate(template.value));
  function renderIssues(state) {
    const entries = state.specIssues;
    if (!entries) {
      issues.innerHTML = "";
      return;
    }
    const rows = [
      ...(entries.errors ?? []).map((item) => ({ ...item, severity: "error" })),

    ];
    if (!rows.length) {
      issues.innerHTML = `<p class="ok">${state.extraction?.stage === "scope" ? "范围信息完整，可以确认并生成其余规格。" : "规格信息完整，可以确认。"}</p>`;
      return;
    }
    issues.innerHTML = rows
      .map(
        (item) => `
        <p class="issue ${escapeHtml(item.severity)}">
          ${escapeHtml(item.message)}
        </p>`,
      )
      .join("");
  }

  return {
    showSource(source) {
      // Original input stays visible in the first editor.
      root.querySelector('[data-role="source"]').value = source;
    },
    setTemplates(list) {
      templates = list;
      template.innerHTML = list
        .map(
          (item) =>
            `<option value="${escapeHtml(item.template_id)}">${escapeHtml(item.name)}</option>`,
        )
        .join("");
    },
    update(state) {
      const locked = Boolean(state.spec?.locked);
      const scopeStage = state.extraction?.stage === "scope";
      const snapshot = locked && state.spec?.draft && /^\s*\{/.test(state.specText) ? describeSnapshot(state.spec.draft) : null;
      const originalSource = state.sourceText || state.extraction?.source || "";
      const sourceValue = originalSource || snapshot?.source || "";
      const rawParts = !state.extraction && originalSource === state.specText
        ? { scope: "", remaining: "" } : state.specParts ?? splitSpecText(state.specText);
      const parts = snapshot ? { scope: snapshot.scope, remaining: snapshot.remaining || rawParts.remaining } : rawParts;
      // Avoid replacing the active editor's value and moving its caret on each keystroke.
      if (source.value !== sourceValue) source.value = sourceValue;
      if (scope.value.trim() !== parts.scope.trim()) scope.value = parts.scope;
      if (text.value.trim() !== parts.remaining.trim()) text.value = parts.remaining;
      button("confirm").textContent = "人工 Review · 确认规格";
      button("plan").hidden = false;
      button("reopen-scope").hidden = !state.extraction?.confirmedScope || scopeStage;
      button("reopen-scope").disabled = state.busy;
      source.readOnly = scope.readOnly = text.readOnly = locked || state.busy;
      button("extract").disabled = state.busy || locked || !sourceValue.trim();
      button("review-full").disabled = state.busy || locked || scopeStage || !parts.remaining.trim();
      button("confirm-scope").disabled = state.busy || locked || !parts.scope.trim();
      button("confirm-scope").textContent = state.busy && scopeStage
        ? "正在生成…" : "确认范围并生成规格";
      button("import-file").disabled = state.busy || locked;
      button("check").disabled = state.busy || !state.specText.trim();
      button("confirm").disabled = state.busy || locked || scopeStage || !parts.remaining.trim();
      button("plan").disabled = state.busy || !locked;
      button("reopen").hidden = !locked;
      template.disabled = state.busy;
      template.value = state.templateId;

      const selected = templates.find((item) => item.template_id === state.templateId);
      const description = root.querySelector('[data-role="template-description"]');
      description.textContent = selected?.description ?? "所选模板会自动设置执行容量。";
      for (const option of root.querySelectorAll('[data-action="load-sample"]')) {
        option.disabled = locked || state.busy;
      }

      ref.textContent = state.spec?.ref
        ? `已确认：${state.spec.ref.spec_id} v${state.spec.ref.version}（只读）`
        : locked
          ? "规格已冻结"
          : scopeStage ? "请核对产品范围和明确排除项，可直接修改" : "规格未确认，生成前需要先确认";

      const report = root.querySelector('[data-role="spec-review"]');
      report.hidden = !state.specReview;
      const review = state.specReview;
      report.innerHTML = review ? `<h3>规格 AI_Review 报告</h3><p>${escapeHtml(review.summary)}</p>
        ${(review.findings ?? []).map(finding=>`<article><strong>${escapeHtml(finding.section)}</strong>
        <p>${escapeHtml(finding.message)}</p><p>建议：${escapeHtml(finding.suggestion)}</p></article>`).join("")}` : "";
      renderIssues(state);
    },
  };
}
