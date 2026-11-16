import { escapeHtml } from "../../shared/dom.js";
import { dependencyIds, topologicalLevels } from "../../shared/graph.js";

/**
 * 依赖图（原型）。
 * 正式 DAG 与批次视图由成员 6 的 features/workflow-graph 实现；这里只保留
 * 选中联动与 P1 的连线编辑，用来验证交互，不作为最终渲染方案。
 */
export function mountGraph(root, { store, actions }) {
  root.innerHTML = `
    <h2>依赖图 <small>（连线编辑为 P1 原型）</small></h2>
    <div class="ws-row">
      <button type="button" data-action="link-mode">开始连线</button>
      <span class="ws-meta" data-role="hint">点击节点查看详情</span>
    </div>
    <div class="ws-graph" data-role="stage">
      <svg data-role="svg" aria-hidden="true"></svg>
      <div data-role="board"></div>
    </div>
  `;

  const stage = root.querySelector('[data-role="stage"]');
  const svg = root.querySelector('[data-role="svg"]');
  const board = root.querySelector('[data-role="board"]');
  const hint = root.querySelector('[data-role="hint"]');
  const linkButton = root.querySelector('[data-action="link-mode"]');

  const COL_W = 198;
  const ROW_H = 82;
  const PAD = 14;
  const CARD_W = 166;
  const CARD_H = 58;

  let linkMode = false;
  let source = null;
  let signature = "";

  linkButton.addEventListener("click", () => {
    linkMode = !linkMode;
    source = null;
    signature = "";
    store.dispatch({
      type: "MESSAGE",
      message: {
        level: "info",
        text: linkMode ? "连线模式：先点前置任务，再点当前任务" : "已退出连线模式",
      },
    });
  });

  board.addEventListener("click", (event) => {
    const node = event.target.closest("[data-task]");
    if (!node) return;
    const id = node.dataset.task;
    if (!linkMode) {
      store.dispatch({ type: "SELECT_TASK", taskId: id });
      return;
    }
    if (!source) {
      source = id;
      signature = "";
      store.dispatch({ type: "MESSAGE", message: { level: "info", text: `已选择 ${id}，再点目标任务完成连线` } });
      return;
    }
    if (source === id) {
      source = null;
      signature = "";
      return;
    }
    actions.link(source, id);
    source = null;
    signature = "";
  });

  function columnsOf(state) {
    if (state.execution?.compiled && state.execution.batches?.length) {
      return state.execution.batches.map((batch) => batch.tasks);
    }
    return topologicalLevels(state.plan?.tasks ?? []);
  }

  function draw(state) {
    const tasks = state.plan?.tasks ?? [];
    linkButton.textContent = linkMode ? "结束连线" : "开始连线";
    linkButton.classList.toggle("active", linkMode);
    if (!tasks.length) {
      board.innerHTML = "";
      svg.innerHTML = "";
      stage.style.width = "100%";
      stage.style.height = "160px";
      hint.textContent = "尚未生成计划";
      return;
    }

    const columns = columnsOf(state);
    const position = new Map();
    columns.forEach((column, ci) =>
      column.forEach((id, ri) => {
        if (tasks.some((task) => task.id === id)) position.set(id, { ci, ri });
      }),
    );
    const rows = Math.max(1, ...columns.map((column) => column.length));
    const width = Math.max(columns.length * COL_W, 320) + PAD * 2;
    const height = rows * ROW_H + PAD * 2;
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    stage.style.overflow = "auto";

    const paths = [];
    for (const task of tasks) {
      const to = position.get(task.id);
      if (!to) continue;
      for (const dep of dependencyIds(task)) {
        const from = position.get(dep);
        if (!from) continue;
        const x1 = PAD + from.ci * COL_W + CARD_W;
        const y1 = PAD + from.ri * ROW_H + CARD_H / 2;
        const x2 = PAD + to.ci * COL_W;
        const y2 = PAD + to.ri * ROW_H + CARD_H / 2;
        paths.push(
          `<path class="ws-edge" d="M ${x1} ${y1} C ${x1 + 26} ${y1}, ${x2 - 26} ${y2}, ${x2} ${y2}" marker-end="url(#ws-arrow)"/>`,
        );
      }
    }

    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = `
      <defs>
        <marker id="ws-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0 0 L10 5 L0 10z" fill="#7489ad"/>
        </marker>
      </defs>
      ${paths.join("")}`;

    board.innerHTML = tasks
      .map((task) => {
        const pos = position.get(task.id);
        if (!pos) return "";
        const classes = [
          "ws-node",
          task.role,
          task.id === state.selectedTaskId ? "selected" : "",
          task.id === source ? "source" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const left = PAD + pos.ci * COL_W;
        const top = PAD + pos.ri * ROW_H;
        return `<button type="button" class="${escapeHtml(classes)}" data-task="${escapeHtml(task.id)}"
          style="left:${left}px;top:${top}px;width:${CARD_W}px;height:${CARD_H}px">
          <b>${escapeHtml(task.title)}</b>
          <small>${escapeHtml(task.id)} · ${escapeHtml(task.role)}</small>
        </button>`;
      })
      .join("");

    hint.textContent = linkMode
      ? source
        ? `已选择 ${source}，点目标任务完成连线`
        : "连线模式：先点前置任务，再点当前任务"
      : "点击节点查看详情";
  }

  return {
    update(state) {
      const next = [
        state.plan?.revision,
        state.plan?.tasks?.length,
        state.selectedTaskId,
        state.execution?.plan_ref?.revision,
        state.phase,
        linkMode,
        source,
      ].join("|");
      if (next === signature) return;
      signature = next;
      draw(state);
    },
  };
}
