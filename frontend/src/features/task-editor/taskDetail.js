import { escapeHtml } from "../../shared/dom.js";

const ROLE_OPTIONS = ["backend", "frontend", "qa"];
const STAGE_OPTIONS = ["design", "implement", "verify", "integrate"];

function linesOf(value) {
  return (Array.isArray(value) ? value : [value ?? ""]).map((item) => String(item)).join("\n");
}

export function mountTaskDetail(root, { store, actions }) {
  let signature = "";

  root.addEventListener("change", (event) => {
    const taskId = root.dataset.task;
    if (!taskId) return;
    const field = event.target.closest("[data-field]");
    if (field) {
      const name = field.dataset.field;
      const value =
        name === "acceptance"
          ? field.value.split("\n").map((line) => line.trim()).filter(Boolean)
          : name === "priority"
            ? Number(field.value)
            : field.value;
      actions.patchTask(taskId, { [name]: value });
      return;
    }
    if (event.target.closest('[data-role="add-dep"]')) {
      const from = event.target.value;
      if (from) actions.link(from, taskId);
    }
  });

  root.addEventListener("click", (event) => {
    const taskId = root.dataset.task;
    if (!taskId) return;
    const remove = event.target.closest("[data-remove-dep]");
    if (remove) {
      actions.unlink(remove.dataset.removeDep, taskId);
      return;
    }
    if (event.target.closest('[data-action="delete"]')) {
      if (window.confirm(`删除任务 ${taskId}？引用它的依赖会一并移除，并产生新修订。`)) {
        actions.deleteTask(taskId);
      }
    }
  });

  return {
    update(state) {
      const task = (state.plan?.tasks ?? []).find((item) => item.id === state.selectedTaskId);
      const next = `${state.selectedTaskId}|${state.plan?.revision}|${state.phase}|${state.stale}`;
      if (!task) {
        if (next !== signature) {
          signature = next;
          root.dataset.task = "";
          root.innerHTML = `<h2>任务详情</h2><p class="ws-meta">选择左侧任务查看验收条件、产物与依赖原因。</p>`;
        }
        return;
      }
      if (next === signature) return;
      signature = next;
      root.dataset.task = task.id;

      const deps = task.depends_on ?? [];
      const depIds = deps.map((dep) => (typeof dep === "string" ? dep : dep.task_id));
      const candidates = (state.plan?.tasks ?? [])
        .filter((item) => item.id !== task.id && !depIds.includes(item.id))
        .map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.id)} ${escapeHtml(item.title)}</option>`)
        .join("");

      root.innerHTML = `
        <h2>任务详情 <code>${escapeHtml(task.id)}</code></h2>
        <label>标题
          <input data-field="title" value="${escapeHtml(task.title)}">
        </label>
        <label>描述
          <textarea data-field="description" rows="2">${escapeHtml(task.description ?? "")}</textarea>
        </label>
        <label>验收条件（每行一条）
          <textarea data-field="acceptance" rows="${Math.max(2, linesOf(task.acceptance).split("\n").length)}">${escapeHtml(linesOf(task.acceptance))}</textarea>
        </label>
        <div class="ws-row">
          <label>角色
            <select data-field="role">
              ${ROLE_OPTIONS.map((role) => `<option ${role === task.role ? "selected" : ""}>${role}</option>`).join("")}
            </select>
          </label>
          <label>阶段
            <select data-field="stage">
              ${STAGE_OPTIONS.map((stage) => `<option ${stage === task.stage ? "selected" : ""}>${stage}</option>`).join("")}
            </select>
          </label>
          <label>优先级
            <input data-field="priority" type="number" min="0" max="9" value="${Number(task.priority ?? 0)}">
          </label>
        </div>
        <h3>前置任务</h3>
        <ul class="ws-deps">
          ${
            deps
              .map((dep) => {
                const id = typeof dep === "string" ? dep : dep.task_id;
                const reason = typeof dep === "string" ? "" : dep.reason ?? "";
                return `<li><code>${escapeHtml(id)}</code> ${escapeHtml(reason)}
                  <button type="button" data-remove-dep="${escapeHtml(id)}" title="删除该依赖">移除</button></li>`;
              })
              .join("") || "<li class='ws-meta'>无前置，只依赖设计规格</li>"
          }
        </ul>
        <label>添加前置（接线等价于画布连线）
          <select data-role="add-dep"><option value="">选择任务…</option>${candidates}</select>
        </label>
        <h3>产物与追溯</h3>
        <p class="ws-meta">输入：${escapeHtml(
          (task.inputs ?? []).map((item) => `${item.source}:${item.ref}`).join("、") || "无",
        )}</p>
        <p class="ws-meta">产物：${escapeHtml(
          (task.outputs ?? []).map((item) => `${item.id}(${item.type})`).join("、") || "无",
        )}</p>
        <p class="ws-meta">需求：${escapeHtml((task.requirement_ids ?? []).join("、") || "未关联")} ｜ 模块：${escapeHtml(
          (task.module_ids ?? []).join("、") || "未关联",
        )} ｜ 设计引用：${escapeHtml((task.design_refs ?? []).join("、") || "无")}</p>
        <p class="ws-meta">类型：${escapeHtml(task.kind ?? "implementation")} ｜ 估算：${escapeHtml(
          task.estimated_effort
            ? `${task.estimated_effort.value} ${task.estimated_effort.unit}${task.estimated_effort.basis ? `（${task.estimated_effort.basis}）` : ""}`
            : "未估算",
        )}</p>
        <div class="ws-row">
          <button type="button" data-action="delete" class="danger">删除任务</button>
        </div>
      `;
    },
  };
}
