import { escapeHtml } from "../../shared/dom.js";

export function mountBatchPanel(root, { store }) {
  root.innerHTML = `
    <h2>校验与批次</h2>
    <div data-role="report"></div>
    <div data-role="batches"></div>
  `;

  const report = root.querySelector('[data-role="report"]');
  const batches = root.querySelector('[data-role="batches"]');

  root.addEventListener("click", (event) => {
    const item = event.target.closest("[data-task]");
    if (item) store.dispatch({ type: "SELECT_TASK", taskId: item.dataset.task });
  });

  return {
    update(state) {
      if (!state.validation) {
        report.innerHTML = `<p class="ws-meta">尚未校验。先确认规格并生成计划，再执行校验。</p>`;
      } else {
        const blocking = state.validation.blocking ?? [];
        const warnings = state.validation.warnings ?? [];
        report.innerHTML = `
          <p class="ws-meta">报告修订：${escapeHtml(state.validation.plan_ref?.revision)} ｜ 阻断 ${blocking.length} ｜ 警告 ${warnings.length}</p>
          ${blocking
            .map(
              (issue) => `<p class="issue error" data-task="${escapeHtml(issue.object_id)}">
                <code>${escapeHtml(issue.code)}</code> ${escapeHtml(issue.message)}
                <span class="path">${escapeHtml(issue.field_path ?? "")}</span></p>`,
            )
            .join("")}
          ${warnings
            .map(
              (issue) => `<p class="issue warning" data-task="${escapeHtml(issue.object_id)}">
                <code>${escapeHtml(issue.code)}</code> ${escapeHtml(issue.message)}</p>`,
            )
            .join("")}
        `;
      }

      if (!state.execution) {
        batches.innerHTML = `<p class="ws-meta">尚未编排。</p>`;
        return;
      }
      if (state.execution.compiled === false) {
        batches.innerHTML = `
          <p class="ws-meta">编排被阻塞，${state.execution.blocked.length} 个任务未安排：</p>
          <ul>${state.execution.blocked
            .map((item) => `<li><code>${escapeHtml(item.task_id)}</code> ${escapeHtml(item.reason)}</li>`)
            .join("")}</ul>`;
        return;
      }
      batches.innerHTML = `
        <p class="ws-meta">${state.execution.batches.length} 个批次 ｜ 执行计划 ${escapeHtml(
          state.execution.plan_ref?.execution_plan_id ?? "",
        )}</p>
        ${state.execution.batches
          .map(
            (batch) => `
            <div class="ws-batch">
              <b>批次 ${batch.index + 1}</b>
              ${batch.tasks
                .map((id) => `<button type="button" class="chip" data-task="${escapeHtml(id)}">${escapeHtml(id)}</button>`)
                .join("")}
              ${batch.waiting
                .map((item) => `<span class="waiting" title="${escapeHtml(item.reason)}">${escapeHtml(item.task_id)} 等待：${escapeHtml(item.reason)}</span>`)
                .join("")}
            </div>`,
          )
          .join("")}
      `;
    },
  };
}
