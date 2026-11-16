import { escapeHtml } from "../../shared/dom.js";

function issuesByTask(state) {
  const map = new Map();
  const push = (issue) => {
    for (const id of [issue.object_id, ...(issue.related_ids ?? [])]) {
      if (!id) continue;
      const list = map.get(id) ?? [];
      list.push(issue);
      map.set(id, list);
    }
  };
  for (const issue of state.validation?.blocking ?? []) push({ ...issue, severity: "error" });
  for (const issue of state.validation?.warnings ?? []) push({ ...issue, severity: "warning" });
  return map;
}

export function mountTaskTable(root, { store, actions }) {
  root.innerHTML = `
    <h2>任务列表 <small data-role="count"></small></h2>
    <div class="ws-row">
      <button type="button" data-action="add">新增任务</button>
      <span class="ws-meta" data-role="hint"></span>
    </div>
    <div class="ws-table-wrap">
      <table class="ws-table">
        <thead><tr><th>ID</th><th>标题</th><th>角色</th><th>阶段</th><th>前置</th><th>问题</th></tr></thead>
        <tbody data-role="body"></tbody>
      </table>
    </div>
  `;

  const body = root.querySelector('[data-role="body"]');
  const count = root.querySelector('[data-role="count"]');
  const hint = root.querySelector('[data-role="hint"]');
  const addButton = root.querySelector('[data-action="add"]');

  root.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-task]");
    if (row) {
      store.dispatch({ type: "SELECT_TASK", taskId: row.dataset.task });
      return;
    }
    if (event.target.closest('[data-action="add"]')) {
      actions.addTask();
    }
  });

  return {
    update(state) {
      const tasks = state.plan?.tasks ?? [];
      addButton.disabled = !state.plan || state.busy;
      count.textContent = tasks.length ? `${tasks.length} 项` : "";
      hint.textContent = state.stale ? "任务已修改，等待重新编排" : "";
      hint.className = `ws-meta ${state.stale ? "warn" : ""}`;

      if (!tasks.length) {
        body.innerHTML = `<tr><td colspan="6" class="empty">尚未生成计划</td></tr>`;
        return;
      }

      const issues = issuesByTask(state);
      body.innerHTML = tasks
        .map((task) => {
          const deps = (task.depends_on ?? [])
            .map((dep) => (typeof dep === "string" ? dep : dep.task_id))
            .join("、");
          const own = issues.get(task.id) ?? [];
          const errorCount = own.filter((item) => item.severity === "error").length;
          const warnCount = own.length - errorCount;
          const badge = errorCount
            ? `<span class="badge error">${errorCount} 错误</span>`
            : warnCount
              ? `<span class="badge warn">${warnCount} 警告</span>`
              : `<span class="badge ok">通过</span>`;
          return `
            <tr data-task="${escapeHtml(task.id)}" ${task.id === state.selectedTaskId ? 'aria-selected="true"' : ""}>
              <td><code>${escapeHtml(task.id)}</code></td>
              <td>${escapeHtml(task.title)}</td>
              <td><span class="role ${escapeHtml(task.role)}">${escapeHtml(task.role)}</span></td>
              <td>${escapeHtml(task.stage)}</td>
              <td>${escapeHtml(deps || "设计规格")}</td>
              <td>${badge}</td>
            </tr>`;
        })
        .join("");
    },
  };
}
