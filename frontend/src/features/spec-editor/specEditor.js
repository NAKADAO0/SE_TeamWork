import { escapeHtml } from "../../shared/dom.js";

/**
 * 规格与配置视图（成员 1）。
 * 只负责渲染与派发意图；所有状态变化走 reducer，所有请求走 api。
 */
export function mountSpecEditor(root, { store, actions }) {
  root.innerHTML = `
    <h2>规格与配置</h2>
    <div class="ws-row">
      <label class="grow">样例
        <select data-role="sample">
          <option value="chat">聊天 MVP</option>
          <option value="community">内容社区 MVP（未接入）</option>
        </select>
      </label>
      <button type="button" data-action="load">载入样例</button>
    </div>
    <textarea data-role="text" spellcheck="false" aria-label="设计规格 JSON"></textarea>
    <div class="ws-row">
      <button type="button" data-action="check">检查缺口</button>
      <button type="button" data-action="confirm">确认规格</button>
      <button type="button" data-action="reopen" hidden>修改</button>
      <button type="button" data-action="plan" class="primary">生成计划</button>
    </div>
    <div data-role="issues" class="ws-issues"></div>
    <p data-role="ref" class="ws-meta"></p>
    <div class="ws-row">
      <label class="grow">模板 <select data-role="template"></select></label>
      <label>全局并发 <input data-role="concurrency" type="number" min="1" max="6" step="1"></label>
    </div>
    <div class="ws-row" data-role="caps"></div>
  `;

  const sample = root.querySelector('[data-role="sample"]');
  const text = root.querySelector('[data-role="text"]');
  const issues = root.querySelector('[data-role="issues"]');
  const ref = root.querySelector('[data-role="ref"]');
  const template = root.querySelector('[data-role="template"]');
  const concurrency = root.querySelector('[data-role="concurrency"]');
  const caps = root.querySelector('[data-role="caps"]');
  const button = (name) => root.querySelector(`[data-action="${name}"]`);

  let lastToken = -1;
  let templates = [];
  let capsSignature = "";

  root.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target || target.disabled) return;
    const action = target.dataset.action;
    if (action === "load") actions.loadSample(sample.value);
    if (action === "check") actions.checkSpec();
    if (action === "confirm") actions.confirmSpec();
    if (action === "reopen") store.dispatch({ type: "SPEC_REOPEN" });
    if (action === "plan") actions.startPlanning();
  });

  text.addEventListener("input", () => {
    store.dispatch({ type: "SPEC_TEXT_CHANGE", text: text.value });
  });

  template.addEventListener("change", () => actions.setTemplate(template.value));
  concurrency.addEventListener("change", () => {
    actions.setConfig({ max_concurrency: Number(concurrency.value) });
  });
  caps.addEventListener("change", (event) => {
    const field = event.target.closest("[data-role]");
    if (!field) return;
    actions.setConfig({ role_capacity: { [field.dataset.role]: Number(field.value) } });
  });

  function renderIssues(state) {
    const entries = state.specIssues;
    if (!entries) {
      issues.innerHTML = "";
      return;
    }
    const rows = [...entries.errors.map((item) => ({ ...item, severity: "error" })), ...entries.warnings];
    if (!rows.length) {
      issues.innerHTML = `<p class="ok">规格信息完整，可以确认。</p>`;
      return;
    }
    issues.innerHTML = rows
      .map(
        (item) => `
        <p class="issue ${escapeHtml(item.severity)}">
          <code>${escapeHtml(item.code)}</code>
          <span class="path">${escapeHtml(item.field_path)}</span>
          ${escapeHtml(item.message)}
        </p>`,
      )
      .join("");
  }

  return {
    setTemplates(list) {
      templates = list;
      template.innerHTML = list
        .map(
          (item) =>
            `<option value="${escapeHtml(item.template_id)}">${escapeHtml(item.name)} · 并发 ${item.max_concurrency}</option>`,
        )
        .join("");
    },
    update(state) {
      if (lastToken !== state.specToken) {
        text.value = state.specText;
        lastToken = state.specToken;
      }
      const locked = Boolean(state.spec?.locked);
      text.readOnly = locked;
      button("check").disabled = state.busy || !state.spec;
      button("confirm").disabled = state.busy || locked || !state.spec;
      button("plan").disabled = state.busy || !locked;
      button("reopen").hidden = !locked;
      sample.disabled = state.busy;
      template.disabled = state.busy;
      template.value = state.templateId;
      if (document.activeElement !== concurrency) concurrency.value = state.config.max_concurrency;

      const signature = JSON.stringify(state.config.role_capacity);
      if (signature !== capsSignature) {
        capsSignature = signature;
        caps.innerHTML = Object.entries(state.config.role_capacity)
          .map(
            ([role, value]) => `
            <label>${escapeHtml(role)} 容量
              <input data-role="${escapeHtml(role)}" type="number" min="1" max="4" step="1" value="${Number(value)}">
            </label>`,
          )
          .join("");
      }

      ref.textContent = state.spec?.ref
        ? `已确认：${state.spec.ref.spec_id} v${state.spec.ref.version}（只读）`
        : locked
          ? "规格已冻结"
          : "规格未确认，生成前需要先确认";

      renderIssues(state);
      void templates;
    },
  };
}
