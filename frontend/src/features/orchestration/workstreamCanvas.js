import { escapeHtml as safe } from "../../shared/dom.js";
import { makeWorkstreams, workstreamProgress } from "./workstreams.js";

const groupLabel = { waiting:"未开始", active:"进行中", done:"已完成", blocked:"需介入" };
const taskLabel = { waiting:"待执行", running:"执行中", paused:"已暂停", failed:"执行失败", done:"草稿已完成" };
const roleName = { backend:"服务端", frontend:"界面", qa:"测试与验证" };
const groupKey = (key) => key?.startsWith("package:") ? key.slice(8) : null;
export const isPackageStage = (key) => typeof key === "string" && /^package:[A-Za-z0-9_-]{1,20}$/.test(key);

export function edgePath(from, to, opposite, index, width) {
  const spread = opposite ? 7 : 0;
  const forward = from.cx < to.cx;
  if (Math.abs(from.cy - to.cy) < 3) {
    const ax = forward ? from.right + 4 : from.left - 4;
    const bx = forward ? to.left - 4 : to.right + 4;
    if (Math.abs(ax - bx) < 110) {
      const y = from.cy + (forward ? -spread : spread);
      return `M ${ax} ${y} H ${bx}`;
    }
    const ax2 = from.cx, bx2 = to.cx;
    const y = Math.max(from.bottom, to.bottom) + (forward ? 15 : 29);
    return `M ${ax2} ${from.bottom + 4} V ${y} H ${bx2} V ${to.bottom + 4}`;
  }
  const down = from.cy < to.cy;
  const sx = from.cx + (down ? spread : -spread), tx = to.cx + (down ? spread : -spread);
  // Adjacent tall cards still use a curved join. Only routes skipping a row
  // need the outer lane; a fixed threshold misclassified wrapped card text.
  if (Math.abs(from.cy-to.cy) > Math.max(from.bottom-from.top, to.bottom-to.top) + 100) {
    const lane = width - 7 - index % 3 * 7;
    const a = down ? from.bottom + 12 : from.top - 12;
    const b = down ? to.top - 12 : to.bottom + 12;
    return `M ${sx} ${down ? from.bottom + 4 : from.top - 4} V ${a} H ${lane} V ${b} H ${tx} V ${down ? to.top - 4 : to.bottom + 4}`;
  }
  const y1 = down ? from.bottom + 4 : from.top - 4;
  const y2 = down ? to.top - 4 : to.bottom + 4;
  const mid = (y1 + y2) / 2;
  return `M ${sx} ${y1} C ${sx} ${mid}, ${tx} ${mid}, ${tx} ${y2}`;
}

export function mountWorkstreamCanvas(root, onSelect) {
  const get = (q) => root.querySelector(q);
  const empty = get("[data-workstream-empty]");
  const board = get("[data-workstream-board]");
  const caption = get("[data-workstream-caption]");
  const list = get("[data-workstream-handoffs]");
  const detail = get("[data-workstream-detail]");
  let model = makeWorkstreams(null);
  let plan;
  let selected;
  let detailKey;
  let run;
  let cards = new Map();

  function drawEdges() {
    const svg = board.querySelector("svg");
    const frame = board.getBoundingClientRect();
    if (!svg || !frame.width || !frame.height) return;
    svg.setAttribute("viewBox", `0 0 ${frame.width} ${frame.height}`);
    const bounds = new Map([...cards].map(([id, card]) => {
      const rect = card.getBoundingClientRect();
      const left=rect.left-frame.left, top=rect.top-frame.top;
      return [id, {left,top,right:left+rect.width,bottom:top+rect.height,cx:left+rect.width/2,cy:top+rect.height/2}];
    }));
    [...svg.querySelectorAll("path[data-link]")].forEach((path, index) => {
      const {from,to} = model.edges[index];
      const reciprocal = model.edges.some((edge) => edge.from === to && edge.to === from);
      path.setAttribute("d", edgePath(bounds.get(from), bounds.get(to), reciprocal, index, frame.width));
      path.dataset.related = String(selected === from || selected === to);
    });
  }

  const observer = new ResizeObserver(drawEdges);
  observer.observe(board);
  function build() {
    observer.disconnect();
    observer.observe(board);
    const { groups, edges } = model;
    board.hidden = !groups.length;
    list.hidden = !edges.length;
    board.innerHTML = groups.map((group, index) => `
      <button type="button" class="wp-card" data-package="${safe(group.id)}" aria-pressed="false">
        <span class="wp-card-meta"><span>${String(index + 1).padStart(2,"0")} / 工作包</span><span data-package-state></span></span>
        <strong>${safe(group.title)}</strong><span class="wp-card-summary">${safe(group.summary || "点击查看小任务与交接")}</span>
        <progress aria-label="工作包进度" max="${group.tasks.length}" value="0"></progress>
        <span class="wp-card-meta"><span data-package-count></span><span aria-hidden="true">查看 →</span></span>
      </button>`).join("") + (groups.length ? `
      <svg class="wp-edges" aria-hidden="true"><defs>
        <marker id="wp-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10z"/></marker>
      </defs>${edges.map(({from,to}) => `<path data-link data-from="${safe(from)}" data-to="${safe(to)}" marker-end="url(#wp-arrow)"/>`).join("")}</svg>` : "");
    cards = new Map([...board.querySelectorAll("button[data-package]")].map((card) => [card.dataset.package, card]));
    for (const card of cards.values()) observer.observe(card);
    const names = new Map(groups.map((group)=>[group.id,group.title]));
    list.querySelector("ul").innerHTML = edges.map(({from,to,dependencies}) =>
      `<li><strong>${safe(names.get(from))} → ${safe(names.get(to))}</strong><span>${dependencies.length} 条小任务依赖</span></li>`).join("");
    drawEdges();
  }

  function drawDetail(group, progress) {
    const key = group ? `${group.id}|${progress.tasks.map((task)=>`${task.id}:${task.status}`).join("|")}` : "";
    if (key === detailKey) return;
    detailKey = key;
    if (!group) { detail.replaceChildren(); return; }
    const opened = new Set([...detail.querySelectorAll("details[open]")].map((item)=>item.dataset.childTask));
    const handoffs = model.edges.filter(({from,to}) => from === group.id || to === group.id);
    const names = new Map(model.groups.map(({id,title})=>[id,title]));
    detail.innerHTML = `
      <div class="wp-detail-overview"><p>${safe(group.summary || "按小任务追踪工作包进度。")}</p>
        <strong>${progress.complete} / ${progress.total} 项完成</strong><progress aria-label="工作包小任务进度" max="${progress.total}" value="${progress.complete}"></progress>
        <button type="button" data-package-discuss>进入人工讨论 ↓</button></div>
      <h3>小任务清单</h3>
      ${progress.tasks.map((task) => `<details data-child-task="${safe(task.id)}" ${opened.has(task.id) ? "open" : ""}>
        <summary><span><small>${safe(task.id)} · ${safe(roleName[task.role] ?? task.role ?? "")}</small><strong>${safe(task.title)}</strong></span><span class="wp-task-status" data-state="${task.status}">${taskLabel[task.status]}</span></summary>
        <p>${safe(task.description)}</p>
        <p><strong>前置：</strong>${safe((task.depends_on ?? []).map((dep)=>typeof dep==="string"?dep:dep.task_id).join("、") || "无")}</p>
        <p><strong>验收：</strong>${safe([task.acceptance].flat().filter(Boolean).join("；") || "查看规格")}</p>
      </details>`).join("")}
      ${handoffs.length ? `<h3>跨工作包交接</h3><ul class="wp-handoffs">${handoffs.map(({from,to,dependencies}) =>
        `<li><strong>${safe(names.get(from))} → ${safe(names.get(to))}</strong><span>${dependencies.map(({from:a,to:b})=>`${safe(a.id)} → ${safe(b.id)}`).join("；")}</span></li>`).join("")}</ul>`:""}`;
  }

  function renderProgress() {
    let active;
    for (const group of model.groups) {
      const card = cards.get(group.id);
      const progress = workstreamProgress(group, run);
      card.dataset.status = progress.status;
      card.setAttribute("aria-pressed", String(group.id === selected));
      card.querySelector("progress").value = progress.complete;
      card.querySelector("[data-package-state]").textContent = groupLabel[progress.status];
      card.querySelector("[data-package-count]").textContent = `${progress.complete} / ${progress.total} 项完成`;
      if (group.id === selected) active = [group, progress];
    }
    drawDetail(...(active ?? []));
  }

  board.onclick = (event) => {
    const button = event.target.closest("[data-package]");
    if (button) onSelect(`package:${button.dataset.package}`);
  };

  return {
    get(key) { return model.groups.find((group) => group.id === groupKey(key)); },
    focus(id) { cards.get(id)?.focus(); },
    select(key) {
      selected = groupKey(key);
      renderProgress();
      drawEdges();
    },
    update(state, newRun) {
      if (state.plan !== plan) {
        plan = state.plan;
        model = makeWorkstreams(plan);
        detailKey = undefined;
        build();
      }
      run = newRun;
      const hasPlan = model.groups.length > 0;
      empty.hidden = hasPlan;
      empty.querySelector("strong").textContent = state.phase === "planning" ? "AI 正在规划项目工作包…" : "提交已确认的设计规格，生成项目工作包";
      caption.textContent = hasPlan ? `${model.origin === "ai" ? "AI 规划" : "按已有任务归组"} · ${model.groups.length} 个工作包 · ${model.edges.length} 条工作包交接` : "等待规格";
      renderProgress();
    },
  };
}
