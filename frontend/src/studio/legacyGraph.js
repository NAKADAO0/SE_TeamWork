// Extracted from demo/graph.js: retain the established three-pane graph, routing,
// task navigation, detail fields, zoom, panning, collapse and resize interactions.
import { topologicalLevels, findCycle } from "../shared/graph.js";
import { taskState } from "./taskViews.js";
export function mountLegacyGraph(host, onSelect = () => {}) {
const scope = host.attachShadow({mode:"open"});
scope.innerHTML = '<link rel="stylesheet" href="/frontend/src/studio/taskGraph.css"><link rel="stylesheet" href="/frontend/src/studio/legacyGraph.css"><main id="graph-view"><div class="graph-heading"><h2>任务图谱</h2><span id="summary"></span><button id="fullscreen" type="button">全屏</button></div><div class="graph-layout">\n  <div class="panel-shell task-shell"><div class="panel-resizer" data-resize="tasks" role="separator" aria-label="调整任务列表宽度" aria-orientation="vertical" aria-valuemin="180" aria-valuemax="420" aria-valuenow="220" tabindex="0"></div><section id="task-panel" class="task-rail" aria-label="阶段与任务进度"><div id="progress"></div><div id="task-list"></div></section><button type="button" class="panel-handle" id="toggle-tasks" aria-controls="task-panel" aria-expanded="true" aria-label="收起任务列表" title="收起任务列表">‹</button></div>\n  <section class="graph-center" aria-label="工作流画布">\n    <div class="graph-toolbar">\n      <div class="layout-switch" role="group" aria-label="图谱排列方式"><button type="button" data-layout="batches" aria-pressed="true" title="按编排结果分批">批次图</button><button type="button" data-layout="dependencies" aria-pressed="false" title="按前置关系分层">依赖图</button></div>\n      <div class="workspace-zoom" role="group" aria-label="依赖图缩放" title="⌘ / Ctrl + 滚轮缩放"><button type="button" id="zoom-out" aria-label="缩小依赖图">−</button><output id="zoom-value" aria-live="polite">100%</output><button type="button" id="zoom-in" aria-label="放大依赖图">＋</button><button type="button" id="zoom-fit">适应画布</button><button type="button" id="zoom-reset" title="恢复 100%">重置</button></div>\n      <details class="legend-menu"><summary>状态</summary><div id="status-legend" aria-label="任务状态图例"></div></details>\n      <span class="graph-cross-key">跨角色依赖</span>\n    </div>\n    <section id="canvas" aria-label="任务依赖图"></section>\n  </section>\n  <div class="panel-shell detail-shell"><div class="panel-resizer" data-resize="detail" role="separator" aria-label="调整任务详情宽度" aria-orientation="vertical" aria-valuemin="200" aria-valuemax="440" aria-valuenow="248" tabindex="0"></div><button type="button" class="panel-handle" id="toggle-detail" aria-controls="detail" aria-expanded="true" aria-label="收起任务详情" title="收起任务详情">›</button><section id="detail" aria-label="任务详情" aria-live="polite">选择任务查看详情。</section></div>\n</div></main>';
const canvas = scope.querySelector('#canvas');
const view = scope.querySelector('#graph-view');
const toggle = scope.querySelector('#fullscreen');
let selectedId, selectedLayout = "dependencies", signature;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const text = value => Array.isArray(value) ? value.map(item => typeof item === 'object' && item !== null ? item.description ?? item.id ?? JSON.stringify(item) : item).join('；') : value || '未提供';
const roles = { backend: '后端', frontend: '前端', qa: '测试' };

const taskStates = {
  pending: '未开始', running: '进行中', completed: '已完成',
  failed: '失败', blocked: '阻塞', unknown: '未完成',
};
const planStates = { draft: '草稿', validated: '已校验', compiled: '已编排', confirmed: '已确认' };
function statusBadge(status) {
  return `<span class="state-badge state-${status}">${taskStates[status]}</span>`;
}


function render(plan, run) {
  const { tasks, batches } = plan;
  const productName = plan.normalized_spec?.product ?? plan.plan_id ?? "当前计划";
  const preview = true, remote = true, metadata = null;
  const stateMap = {waiting:"pending", running:"running", paused:"blocked", failed:"failed", done:"completed"};
  const execution = Object.fromEntries(tasks.map(task => [task.id, {status:stateMap[taskState(task, run)]}]));
  const statusOf = id => execution[id]?.status ?? "unknown";
  const done = new Set(tasks.filter(t => statusOf(t.id)==="completed").map(t=>t.id));
  scope.querySelector('#status-legend').innerHTML = Object.keys(taskStates).filter(k=>k!=="unknown").map(statusBadge).join("");
  const stageTitles = { design: '设计与定义', implement: '功能实现', verify: '验证与测试', test: '验证与测试', integrate: '集成与交付' };
  const phaseGroups = new Map();
  for (const task of tasks) {
    const stage = task.stage ?? (task.role === 'qa' ? 'verify' : 'implement');
    if (!phaseGroups.has(stage)) phaseGroups.set(stage, { title: stageTitles[stage] ?? stage, tasks: [] });
    phaseGroups.get(stage).tasks.push(task.id);
  }
  const phases = metadata?.phases ?? [...phaseGroups.values()];
  const byId = new Map(tasks.map(task => [task.id, task]));
  const deps = task => (task.depends_on ?? []).map(dep => typeof dep === 'string' ? dep : dep.task_id);
  const labels = new Map();
  phases.forEach((phase, pi) => phase.tasks.forEach((id, ti) => labels.set(id, `Task ${pi + 1}.${ti + 1}`)));
  const countEdges = tasks.reduce((sum, task) => sum + deps(task).length, 0);
  scope.querySelector('#summary').textContent = `${preview ? '样例预览' : remote ? '服务端计划' : '当前本地计划'} · ${productName} · ${tasks.length} 个任务 · ${countEdges} 条依赖 · ${batches.length} 个批次${plan.revision ? ` · 修订 r${plan.revision}` : ''}`;
  if (!preview) scope.querySelector('#return-link').href = 'frontend/';
  const percent = Math.round(done.size / tasks.length * 100);
  scope.querySelector('#progress').innerHTML = `<div class="progress-heading"><h2>总开发进度</h2>${preview ? '' : '<span class="execution-unavailable">暂无数据</span>'}</div><div class="total-value">${preview ? `${done.size}/${tasks.length}` : '—'} <small>${preview ? `已完成 · ${percent}%` : '未完成'}</small></div>${preview ? `<progress value="${done.size}" max="${tasks.length}" aria-label="总开发进度"></progress>` : ''}`;
  const list = scope.querySelector('#task-list');
  list.innerHTML = phases.map((phase, pi) => {
    const ids = phase.tasks.filter(id => byId.has(id));
    const complete = ids.filter(id => done.has(id)).length;
    return `<details class="phase-group" open>
      <summary><span class="phase-heading"><small>${String(pi + 1).padStart(2, '0')}</small><strong>${escape(phase.title)}</strong></span><span class="phase-count" aria-label="已完成 ${preview ? complete : '未知'} 项，共 ${ids.length} 项">${preview ? complete : '—'}<span>/${ids.length}</span></span></summary>
      <div class="phase-tasks">${ids.map(id => `<button type="button" class="task-item" data-task="${escape(id)}" aria-pressed="false"><span class="task-copy"><small>${labels.get(id)} ${statusBadge(statusOf(id))}</small><b>${escape(byId.get(id).title)}</b></span></button>`).join('')}</div>
    </details>`;
  }).join('');

  const board = document.createElement('div');
  board.id = 'board';
  canvas.replaceChildren(board);
  if (new Set(tasks.map(task => task.id)).size !== tasks.length) throw new Error('任务 ID 重复');
  if (tasks.some(task => deps(task).some(dep => !byId.has(dep)))) throw new Error('依赖引用了不存在的任务');
  if (findCycle(tasks)) throw new Error('任务依赖存在环，无法排列');
  const dependencyColumns = topologicalLevels(tasks);
  const batchColumns = batches.map(batch => Array.isArray(batch) ? batch : batch.tasks);
  const scheduledIds = batchColumns.flat();
  if (scheduledIds.length !== tasks.length || new Set(scheduledIds).size !== tasks.length || scheduledIds.some(id => !byId.has(id))) throw new Error('批次需要完整且不重复地包含全部任务');
  scope.querySelector('#summary').textContent = `${tasks.length} 个任务 · ${countEdges} 条依赖 · ${dependencyColumns.length} 个依赖层 · ${batches.length} 个批次${plan.capacity ? ` · 并发 ${plan.capacity}` : ''}${plan.revision ? ` · r${plan.revision}` : ''}`;
  let selectedTaskId = tasks.some(t => t.id === selectedId) ? selectedId : tasks[0].id;
  let layout = selectedLayout;
  const CARD_W = 188, CARD_H = 108, PITCH = 238, ROW = 144, PAD = 16;

  // Round each corner of an orthogonal route without changing its connection points.
  function route(points) {
    let path = `M ${points[0].join(' ')}`;
    for (let i = 1; i < points.length - 1; i++) {
      const previous = points[i - 1], current = points[i], next = points[i + 1];
      const before = Math.hypot(current[0] - previous[0], current[1] - previous[1]);
      const after = Math.hypot(next[0] - current[0], next[1] - current[1]);
      if (!before || !after) continue;
      const radius = Math.min(7, before / 2, after / 2);
      const entry = current.map((value, axis) => value + (previous[axis] - value) * radius / before);
      const exit = current.map((value, axis) => value + (next[axis] - value) * radius / after);
      path += ` L ${entry.join(' ')} Q ${current.join(' ')} ${exit.join(' ')}`;
    }
    return `${path} L ${points.at(-1).join(' ')}`;
  }

  function draw() {
    const columns = (layout === 'dependencies' ? dependencyColumns : batchColumns).map(column => [...column]);
    const positions = new Map();
    // Keep sibling tasks close to their producers; stable input order breaks ties.
    columns.forEach((column, ci) => {
      if (layout === 'dependencies') {
        const center = id => {
          const parents = deps(byId.get(id)).map(dep => positions.get(dep)?.row).filter(row => row !== undefined);
          return parents.length ? parents.reduce((sum, row) => sum + row, 0) / parents.length : 0;
        };
        column.sort((a, b) => center(a) - center(b));
      }
      column.forEach((id, row) => positions.set(id, { col: ci, row }));
    });
    const rows = Math.max(1, ...columns.map(column => column.length));
    const edges = tasks.flatMap(task => deps(task).map(from => ({ from, to: task.id })));
    const longEdges = edges.filter(edge => positions.get(edge.to).col - positions.get(edge.from).col > 1);
    const top = 64 + Math.min(longEdges.length, 16) * 9;
    const width = columns.length * PITCH - (PITCH - CARD_W) + PAD * 2;
    const height = Math.max(300, top + rows * ROW + PAD);
    const point = id => {
      const { col, row } = positions.get(id);
      return [PAD + col * PITCH, top + (row + (rows - columns[col].length) / 2) * ROW];
    };
    board.style.width = `${width}px`;
    board.style.height = `${height}px`;
    board.dataset.layout = layout;
    const paths = edges.map(edge => {
      const [x, y] = point(edge.from), [tx, ty] = point(edge.to);
      const start = [x + CARD_W + 5, y + CARD_H / 2], end = [tx - 5, ty + CARD_H / 2];
      const lane = longEdges.indexOf(edge);
      const points = lane < 0
        ? [start, [x + CARD_W + 25, start[1]], [x + CARD_W + 25, end[1]], end]
        : [start, [x + CARD_W + 16, start[1]], [x + CARD_W + 16, 39 + (lane % 16) * 9], [tx - 24, 39 + (lane % 16) * 9], [tx - 24, end[1]], end];
      const fromRole = byId.get(edge.from)?.role, toRole = byId.get(edge.to)?.role;
      const crossRole = Boolean(fromRole && toRole && fromRole !== toRole);
      return `<path data-from="${escape(edge.from)}" data-to="${escape(edge.to)}" data-cross-role="${crossRole}" d="${route(points)}" fill="none" stroke="#9eacc0" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#tip)"/>`;
    });
    board.innerHTML = `<svg width="${width}" height="${height}" aria-hidden="true"><defs><marker id="tip" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#7489ad"/></marker><marker id="tip-active" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#3657cb"/></marker></defs>${paths.join('')}</svg>${columns.map((column, i) => `<div class="layer-heading" style="left:${PAD + i * PITCH}px;width:${CARD_W}px"><span>${layout === 'dependencies' ? '依赖层' : '执行批次'} ${i + 1}</span><small>${column.length} 个任务</small></div>`).join('')}${tasks.map(task => {
      const [x, y] = point(task.id);
      return `<button type="button" class="node task-state-${statusOf(task.id)}" data-task="${escape(task.id)}" aria-pressed="false" aria-label="${escape(labels.get(task.id))} ${escape(task.title)} ${taskStates[statusOf(task.id)]}" style="left:${x}px;top:${y}px;width:${CARD_W}px;height:${CARD_H}px" title="${escape(task.title)}"><small class="node-id">${labels.get(task.id)}</small><strong>${escape(task.title)}</strong><small>${statusBadge(statusOf(task.id))}</small></button>`;
    }).join('')}`;
    setGraphZoom(graphZoom);
    select(selectedTaskId);
  }
  for (const button of scope.querySelectorAll('[data-layout]')) {
    button.onclick = () => {
      layout = button.dataset.layout; selectedLayout = layout;
      for (const tab of scope.querySelectorAll('[data-layout]')) tab.setAttribute('aria-pressed', String(tab === button));
      draw();
      centerSelected();
    };
  }

  function select(id) {
    const task = byId.get(id);
    if (!task) return;
    selectedTaskId = id; selectedId = id; onSelect(task);
    const previous = deps(task), next = tasks.filter(t => deps(t).includes(id)).map(t => t.id);
    for (const button of view.querySelectorAll('[data-task]')) {
      button.setAttribute('aria-pressed', String(button.dataset.task === id));
      button.classList.toggle('related', previous.includes(button.dataset.task) || next.includes(button.dataset.task));
    }
    for (const path of board.querySelectorAll('path[data-from]')) {
      const active = path.dataset.from === id || path.dataset.to === id;
      const crossRole = path.dataset.crossRole === 'true';
      path.setAttribute('stroke', crossRole ? '#247873' : active ? '#3657cb' : '#94a3b8');
      path.setAttribute('stroke-width', active ? '2.25' : '1.5');
      path.setAttribute('stroke-dasharray', crossRole ? '6 3' : 'none');
      path.setAttribute('marker-end', active ? 'url(#tip-active)' : 'url(#tip)');
      path.style.opacity = active ? '1' : crossRole ? '0.85' : '0.55';
    }
    // Draw selected edges last so crossing neutral routes cannot cover their arrowheads.
    for (const path of board.querySelectorAll('path[data-from]')) {
      if (path.dataset.from === id || path.dataset.to === id) path.parentNode.append(path);
    }
    const names = ids => ids.map(ref => `${labels.get(ref)} ${byId.get(ref)?.title ?? ref}`).join('；') || '无';
    const depReasons = (task.depends_on ?? [])
      .filter(d => typeof d === 'object' && d?.reason)
      .map(d => `${d.task_id}: ${d.reason}`);
    const effortText = task.estimated_effort
      ? `${task.estimated_effort.value} ${task.estimated_effort.unit}${task.estimated_effort.basis ? `（${task.estimated_effort.basis}）` : ''}`
      : null;
    const fields = [
      ['角色', roles[task.role] ?? task.role],
      ['阶段', stageTitles[task.stage] ?? task.stage ?? '未标注'],
      ['所属需求', task.requirement_ids ?? task.source],
      ['前置任务', names(previous)],
      ...(depReasons.length ? [['依赖理由', depReasons]] : []),
      ['后续任务', names(next)],
      ['任务说明', task.description],
      ['输入依据', task.inputs],
      ['交付产物', task.output ?? task.outputs],
      ['验收条件', task.acceptance],
      ...(effortText ? [['工作量估算', effortText]] : []),
    ];
    scope.querySelector('#detail').innerHTML = `<header class="task-detail-heading"><h2>任务详情</h2><button type="button" data-close-task aria-label="关闭任务详情" title="关闭任务详情"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></header><div class="reference">${labels.get(id)} · ${escape(id)}</div><h3>${escape(task.title)}</h3>${statusBadge(statusOf(id))}${execution[id]?.reason ? `<p class="state-reason state-${statusOf(id)}">${escape(execution[id].reason)}</p>` : ''}<dl>${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(text(value))}</dd></div>`).join('')}</dl>`;
  }
  view.onclick = event => {
    if (event.target.closest('[data-close-task]')) {
      setDetailOpen(false);
      scope.querySelector('#board .node[aria-pressed="true"]')?.focus({preventScroll:true});
      return;
    }
    const button = event.target.closest('[data-task]');
    if (button) {
      select(button.dataset.task);
      setDetailOpen(true);
      if (button.classList.contains('task-item')) {
        const node = [...board.querySelectorAll('[data-task]')].find(node => node.dataset.task === button.dataset.task);
        if (node) {
          canvas.scrollLeft = Math.max(0, node.offsetLeft * graphZoom - canvas.clientWidth / 2 + 94 * graphZoom);
          canvas.scrollTop = Math.max(0, node.offsetTop * graphZoom - canvas.clientHeight / 2 + 54 * graphZoom);
        }
      }
      if (detailBelow.matches)
        scope.querySelector('#detail').scrollIntoView({block:'nearest'});
    }
  };
  draw();
}
function updateToggle() { toggle.textContent = document.fullscreenElement || view.classList.contains('expanded') ? '退出全屏' : '全屏'; }
toggle.onclick = async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else if (view.classList.contains('expanded')) view.classList.remove('expanded');
  else { try { await view.requestFullscreen(); } catch { view.classList.add('expanded'); } }
  updateToggle();
};
document.addEventListener('fullscreenchange', updateToggle);
document.addEventListener('keydown', event => { if (event.key === 'Escape') { view.classList.remove('expanded'); updateToggle(); } });
// Scale only the dependency board; task navigation and details keep their size.
const zoomValue = scope.querySelector('#zoom-value');
let graphZoom = 1;
function setGraphZoom(value) {
  graphZoom = Math.min(2, Math.max(0.1, Math.floor(value * 100) / 100));
  const board = scope.querySelector('#board');
  if (board) {
    board.style.zoom = String(graphZoom);
    board.classList.toggle('compact', graphZoom < 0.65);
  }
  zoomValue.value = `${Math.round(graphZoom * 100)}%`;
  scope.querySelector('#zoom-out').disabled = graphZoom <= 0.1;
  scope.querySelector('#zoom-in').disabled = graphZoom >= 2;
}
scope.querySelector('#zoom-out').onclick = () => setGraphZoom(graphZoom - 0.1);
scope.querySelector('#zoom-in').onclick = () => setGraphZoom(graphZoom + 0.1);
scope.querySelector('#zoom-fit').onclick = () => {
  const board = scope.querySelector('#board');
  if (!board) return;
  const style = getComputedStyle(canvas);
  const availableWidth = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const availableHeight = canvas.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  setGraphZoom(Math.min(1, availableWidth / board.offsetWidth, availableHeight / board.offsetHeight));
  canvas.scrollTo(0, 0);
};
scope.querySelector('#zoom-reset').onclick = () => {
  setGraphZoom(1);
  canvas.scrollTo(0, 0);
};
canvas.addEventListener('wheel', event => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const before = graphZoom;
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left - canvas.clientLeft;
  const y = event.clientY - rect.top - canvas.clientTop;
  setGraphZoom(graphZoom + (event.deltaY < 0 ? 0.05 : -0.05));
  canvas.scrollLeft = (canvas.scrollLeft + x) * graphZoom / before - x;
  canvas.scrollTop = (canvas.scrollTop + y) * graphZoom / before - y;
}, { passive: false });
setGraphZoom(1);

// Keep a narrow edge handle visible so collapsed panes can always be restored.
const graphLayout = scope.querySelector('.graph-layout');
const resizeCallbacks = new Set();
function setDetailOpen(open) {
  scope.querySelector('.detail-shell').hidden = !open;
  scope.querySelector('#detail').hidden = !open;
  graphLayout.classList.toggle('detail-collapsed', !open);
  scope.querySelector('#toggle-detail').setAttribute('aria-expanded', String(open));
  if (open) resizeCallbacks.forEach(fn => fn());
}

const detailBelow = { get matches() { return host.clientWidth <= 850; }, addEventListener(type, fn) { resizeCallbacks.add(fn); } };
new ResizeObserver(() => resizeCallbacks.forEach(fn => fn())).observe(host);
for (const [buttonId, panelId, className, label] of [
  ['toggle-tasks', 'task-panel', 'tasks-collapsed', '任务列表'],
  ['toggle-detail', 'detail', 'detail-collapsed', '任务详情'],
]) {
  const button = scope.getElementById(buttonId);
  const panel = scope.getElementById(panelId);
  function updateArrow() {
    const direction = panelId === 'task-panel' ? (panel.hidden ? 'right' : 'left')
      : detailBelow.matches ? (panel.hidden ? 'up' : 'down') : (panel.hidden ? 'left' : 'right');
    const points = { left: '8 2 3 7 8 12', right: '3 2 8 7 3 12', up: '2 9 7 4 12 9', down: '2 4 7 9 12 4' };
    button.dataset.direction = direction;
    button.innerHTML = `<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><polyline points="${points[direction]}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  detailBelow.addEventListener('change', updateArrow);
  updateArrow();
  button.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    graphLayout.classList.toggle(className, panel.hidden);
    button.setAttribute('aria-expanded', String(!panel.hidden));
    button.title = `${panel.hidden ? '展开' : '收起'}${label}`;
    button.setAttribute('aria-label', button.title);
    updateArrow();
  });
}

// Drag empty canvas space to pan. Node clicks and native touch scrolling stay available.
let canvasDrag = null;
canvas.addEventListener('pointerdown', event => {
  if (event.pointerType !== 'mouse' || event.button !== 0 || event.target.closest('button, a')) return;
  const rect = canvas.getBoundingClientRect();
  if (event.clientX >= rect.left + canvas.clientLeft + canvas.clientWidth ||
      event.clientY >= rect.top + canvas.clientTop + canvas.clientHeight) return;
  canvasDrag = {
    pointerId: event.pointerId, x: event.clientX, y: event.clientY,
    left: canvas.scrollLeft, top: canvas.scrollTop,
  };
  canvas.setPointerCapture(event.pointerId);
  canvas.classList.add('is-panning');
  event.preventDefault();
});
canvas.addEventListener('pointermove', event => {
  if (!canvasDrag || event.pointerId !== canvasDrag.pointerId) return;
  canvas.scrollLeft = canvasDrag.left - (event.clientX - canvasDrag.x);
  canvas.scrollTop = canvasDrag.top - (event.clientY - canvasDrag.y);
});
function stopCanvasDrag(event) {
  if (!canvasDrag || event.pointerId !== canvasDrag.pointerId) return;
  canvasDrag = null;
  canvas.classList.remove('is-panning');
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
}
canvas.addEventListener('pointerup', stopCanvasDrag);
canvas.addEventListener('pointercancel', stopCanvasDrag);
canvas.addEventListener('lostpointercapture', stopCanvasDrag);

// Resize expanded side panes while reserving usable space for the graph.
for (const handle of scope.querySelectorAll('[data-resize]')) {
  const left = handle.dataset.resize === 'tasks';
  const shell = handle.parentElement;
  const vertical = () => !left && detailBelow.matches;
  let drag = null;
  function updateOrientation() {
    const height = vertical();
    handle.setAttribute('aria-orientation', height ? 'horizontal' : 'vertical');
    handle.setAttribute('aria-label', `调整${left ? '任务列表' : '任务详情'}${height ? '高度' : '宽度'}`);
    handle.setAttribute('aria-valuemin', String(height ? 120 : left ? 180 : 200));
    handle.setAttribute('aria-valuemax', String(height ? 480 : left ? 420 : 560));
    handle.setAttribute('aria-valuenow', String(Math.round(height ? shell.getBoundingClientRect().height : shell.getBoundingClientRect().width)));
  }
  detailBelow.addEventListener('change', updateOrientation);
  updateOrientation();
  function setSize(value) {
    const height = vertical();
    const min = height ? 120 : left ? 180 : 200;
    const other = scope.querySelector(left ? '.detail-shell' : '.task-shell');
    const beside = !detailBelow.matches;
    const room = height ? graphLayout.clientHeight - 400 - 8
      : graphLayout.clientWidth - (beside ? other.getBoundingClientRect().width : 0) - 280 - 16;
    const max = Math.max(min, Math.min(height ? 480 : left ? 420 : 560, room));
    const size = Math.min(max, Math.max(min, Math.round(value)));
    graphLayout.style.setProperty(height ? '--detail-height' : left ? '--task-width' : '--detail-width', `${size}px`);
    handle.setAttribute('aria-valuenow', String(size));
    handle.setAttribute('aria-valuemax', String(Math.floor(max)));
  }
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    const height = vertical();
    drag = { id: event.pointerId, position: height ? event.clientY : event.clientX,
      size: height ? shell.getBoundingClientRect().height : shell.getBoundingClientRect().width, height };
    handle.setPointerCapture(event.pointerId);
    handle.classList.add('is-resizing');
    document.body.classList.add('resizing-panel');
    document.body.classList.toggle('resizing-panel-height', height);
    event.preventDefault();
  });
  handle.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    if (drag.height !== vertical()) { finish(event); return; }
    const position = drag.height ? event.clientY : event.clientX;
    setSize(drag.size + (position - drag.position) * (left ? 1 : -1));
  });
  function finish(event) {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    handle.classList.remove('is-resizing');
    document.body.classList.remove('resizing-panel', 'resizing-panel-height');
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
  }
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', finish);
  handle.addEventListener('lostpointercapture', finish);
  handle.addEventListener('keydown', event => {
    const height = vertical();
    if (!(height ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight']).includes(event.key)) return;
    event.preventDefault();
    const size = height ? shell.getBoundingClientRect().height : shell.getBoundingClientRect().width;
    const positive = height ? event.key === 'ArrowDown' : event.key === 'ArrowRight';
    setSize(size + (positive ? 10 : -10) * (left ? 1 : -1));
  });
}

function centerSelected() {
 const node = scope.querySelector('#board .node[aria-pressed="true"]');
 if(node) { canvas.scrollLeft = Math.max(0,node.offsetLeft*graphZoom-canvas.clientWidth/2+94*graphZoom); canvas.scrollTop = Math.max(0,node.offsetTop*graphZoom-canvas.clientHeight/2+54*graphZoom); }
}
setDetailOpen(false);
return {
 reset() { selectedId=undefined; signature=undefined; setDetailOpen(false); },
 update(plan, execution, run) {
   const next = JSON.stringify([plan, execution, run?.id, run?.cursor, run?.status, run?.results]);
   if (next === signature) return;
   const initial = signature === undefined;
   signature = next;
   const scroll = [canvas.scrollLeft, canvas.scrollTop];
   const closed = [...scope.querySelectorAll('.phase-group')].map((d,i)=>d.open ? -1 : i);
   render({...plan, batches:execution.batches, capacity:execution.config?.max_concurrency}, run);
   scope.querySelectorAll('.phase-group').forEach((d,i)=> { if(closed.includes(i)) d.open=false; });
   canvas.scrollLeft=scroll[0]; canvas.scrollTop=scroll[1];
   if(initial) centerSelected();
 },
 select(id) { scope.querySelector(`[data-task="${CSS.escape(id)}"]`)?.click(); },
 setLayout(value) { scope.querySelector(`[data-layout="${value}"]`)?.click(); },
};
}
