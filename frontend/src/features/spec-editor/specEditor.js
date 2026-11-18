import { escapeHtml } from "../../shared/dom.js";

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
    <textarea data-role="text" spellcheck="false" aria-label="设计规格 JSON 或 Markdown"
      placeholder="粘贴 DesignSpec JSON 或 Markdown（# 产品名称 / ## 产品范围 / ## 需求条目 / ## 模块 / ## 设计条目 / ## 约束）"></textarea>
    <div class="ws-row">
      <button type="button" data-action="extract">AI 整理</button>
      <button type="button" data-action="check">检查缺口</button>
      <button type="button" data-action="confirm">确认规格</button>
      <button type="button" data-action="reopen" hidden>修改</button>
      <button type="button" data-action="plan" class="primary">生成计划</button>
    </div>
    <details class="studio-settings" data-role="source-panel" hidden>
      <summary>查看整理前的说明书</summary>
      <pre data-role="source-text"></pre>
    </details>
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
  const text = root.querySelector('[data-role="text"]');
  const issues = root.querySelector('[data-role="issues"]');
  const ref = root.querySelector('[data-role="ref"]');
  const template = root.querySelector('[data-role="template"]');
  const button = (name) => root.querySelector(`[data-action="${name}"]`);

  let lastToken = -1;
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
      text.focus();
    }
    if (action === "import-file") fileInput.click();
    if (action === "extract") actions.extractSpec();
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

  text.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !samples.hidden) {
      samples.hidden = true;
      return;
    }
    if (event.key === "/" && !event.isComposing && !event.ctrlKey && !event.metaKey &&
        !text.readOnly && !store.getState().busy && !text.value.trim()) {
      event.preventDefault();
      samples.hidden = false;
      samples.querySelector("button").focus();
    }
  });
  samples.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !samples.hidden) {
      samples.hidden = true;
      text.focus();
    }
  });

  text.addEventListener("input", () => {
    store.dispatch({ type: "SPEC_TEXT_CHANGE", text: text.value });
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
      issues.innerHTML = `<p class="ok">规格信息完整，可以确认。</p>`;
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
      root.querySelector('[data-role="source-panel"]').hidden = false;
      root.querySelector('[data-role="source-text"]').textContent = source;
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
      if (lastToken !== state.specToken) {
        text.value = state.specText;
        lastToken = state.specToken;
      }
      const locked = Boolean(state.spec?.locked);
      text.readOnly = locked || state.busy;
      button("extract").disabled = state.busy || locked || !state.specText.trim();
      button("import-file").disabled = state.busy || locked;
      button("check").disabled = state.busy || !state.specText.trim();
      button("confirm").disabled = state.busy || locked || !state.specText.trim();
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
          : "规格未确认，生成前需要先确认";

      renderIssues(state);
    },
  };
}
