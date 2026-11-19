import { enhanceSelect } from './graph-select.js';
import { topologicalLevels, findCycle } from '../frontend/src/shared/graph.js';
import { loadRemoteGraph } from './graph-api.js';
import { exampleVersions } from './graph-versions.js';
import { sampleProgress } from './graph-progress.js';
import { rememberGraph } from './graph-navigation.js';
import { updateGraphLinks } from './site-navigation.js';
const canvas = document.querySelector('#canvas');
const view = document.querySelector('#graph-view');
const toggle = document.querySelector('#fullscreen');
for (const id of ['graph-sample', 'graph-version']) enhanceSelect(document.getElementById(id));
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

async function show() {
  const params = new URLSearchParams(location.search);
  const remote = params.has('api') ? await loadRemoteGraph(params) : null;
  const key = params.get('sample');
  const stored = sessionStorage.getItem('se.plan');
  const preview = !remote && (['chat', 'community', 'complex'].includes(key) || !stored);
  if (!remote && key === 'current' && !stored) throw new Error('当前标签页没有已编排计划，请先到工作台编排');
  const sample = ['chat', 'community', 'complex'].includes(key) ? key : 'chat';
  const response = preview ? await fetch(`examples/${sample}-plan.json`) : null;
  if (response && !response.ok) throw new Error('样例读取失败');
  const sourcePlan = remote ? remote.plan : preview ? await response.json() : JSON.parse(stored);
  const versions = remote ? remote.revisions : preview ? exampleVersions(sourcePlan) : [sourcePlan];
  const requestedRevision = new URLSearchParams(location.search).get('revision');
  const plan = remote ? sourcePlan : requestedRevision === null ? versions[0] : versions.find(item => String(item.revision ?? 1) === requestedRevision);
  if (!plan) throw new Error('该计划版本不存在，请重新选择计划');
  const versionPicker = document.querySelector('#graph-version');
  versionPicker.replaceChildren(...versions.map((item, index) => new Option(
    `r${item.revision ?? 1}${index === 0 ? preview || remote ? ' · 最新' : ' · 当前' : ''}`,
    String(item.revision ?? 1),
  )));
  versionPicker.value = String(plan.revision ?? 1);
  versionPicker.dispatchEvent(new Event('graph-select-sync'));
  versionPicker.addEventListener('change', () => {
    const url = new URL(location.href);
    if (remote) url.searchParams.set('plan_id', remote.planId);
    else url.searchParams.set('sample', preview ? sample : 'current');
    url.searchParams.set('revision', versionPicker.value);
    location.href = url.href;
  });
  const { tasks, batches } = plan;
  const spec = plan.spec ?? plan.normalized_spec ?? {};
  const productObj = spec.product ?? plan.spec_ref?.spec_id ?? plan.plan_id ?? '产品';
  const productName = typeof productObj === 'object' ? (productObj?.name ?? '产品') : productObj;
  if (!Array.isArray(tasks) || !tasks.length || !Array.isArray(batches)) throw new Error('计划缺少任务或批次');
  const picker = document.querySelector('#graph-sample');
  if (remote) picker.replaceChildren(...remote.plans.map(item => new Option(item.title, item.plan_id)));
  if (!remote && stored) picker.add(new Option('当前计划', 'current'));
  picker.value = remote ? remote.planId : preview ? sample : 'current';
  picker.dispatchEvent(new Event('graph-select-sync'));
  picker.addEventListener('change', () => {
    const url = new URL(location.href);
    if (remote) url.searchParams.set('plan_id', picker.value);
    else if (picker.value === 'current') url.searchParams.set('sample', 'current');
    else url.searchParams.set('sample', picker.value);
    url.searchParams.delete('revision');
    location.href = url.href;
  });
  if (remote && !plan.batches?.length) throw new Error(`修订 r${plan.revision} 尚未编排，请先在工作台编排`);
  const currentUrl = new URL(location.href);
  if (remote) currentUrl.searchParams.set('plan_id', remote.planId);
  else currentUrl.searchParams.set('sample', preview ? sample : 'current');
  currentUrl.searchParams.set('revision', plan.revision ?? 1);
  try { rememberGraph(currentUrl, sessionStorage); } catch { /* Storage is optional. */ }
  updateGraphLinks();
  const metadata = preview ? sampleProgress[sample] : null;
  const execution = metadata?.execution ?? {};
  const statusOf = id => Object.hasOwn(taskStates, execution[id]?.status) ? execution[id].status : 'unknown';
  const done = new Set(tasks.filter(task => statusOf(task.id) === 'completed').map(task => task.id));
  const modeNames = {
    live_llm: '实时 PM',
    offline_rule: '离线规则',
    fixture_replay: '样例回放',
    curated_example: '预编排案例',
  };
  const planStatus = metadata?.planStatus ?? plan.status;
  const modeLabel = modeNames[plan.generation_mode ?? plan.mode] ?? '';
  const statusSummary = document.querySelector('#plan-status');
  statusSummary.innerHTML = `<span class="plan-badge plan-${Object.hasOwn(planStates, planStatus) ? planStatus : 'unknown'}">${planStates[planStatus] ?? '未提供'}</span>${modeLabel ? `<span class="plan-badge plan-draft">${escape(modeLabel)}</span>` : ''}${preview ? '' : '<span class="execution-unavailable">规划视图</span>'}`;
  document.querySelector('#status-legend').innerHTML = `<span>任务状态</span>${['pending', 'running', 'completed', 'failed', 'blocked'].map(statusBadge).join('')}`;
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
  document.querySelector('#summary').textContent = `${preview ? '样例预览' : remote ? '服务端计划' : '当前本地计划'} · ${productName} · ${tasks.length} 个任务 · ${countEdges} 条依赖 · ${batches.length} 个批次${plan.revision ? ` · 修订 r${plan.revision}` : ''}`;
  if (!preview) document.querySelector('#return-link').href = 'frontend/';
  const percent = Math.round(done.size / tasks.length * 100);
  document.querySelector('#progress').innerHTML = `<div class="progress-heading"><h2>总开发进度</h2>${preview ? '' : '<span class="execution-unavailable">暂无数据</span>'}</div><div class="total-value">${preview ? `${done.size}/${tasks.length}` : '—'} <small>${preview ? `已完成 · ${percent}%` : '未完成'}</small></div>${preview ? `<progress value="${done.size}" max="${tasks.length}" aria-label="模拟总开发进度"></progress>` : ''}`;
  const list = document.querySelector('#task-list');
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
  canvas.append(board);
  if (new Set(tasks.map(task => task.id)).size !== tasks.length) throw new Error('任务 ID 重复');
  if (tasks.some(task => deps(task).some(dep => !byId.has(dep)))) throw new Error('依赖引用了不存在的任务');
  if (findCycle(tasks)) throw new Error('任务依赖存在环，无法排列');
  const dependencyColumns = topologicalLevels(tasks);
  const batchColumns = batches.map(batch => Array.isArray(batch) ? batch : batch.tasks);
  const scheduledIds = batchColumns.flat();
  if (scheduledIds.length !== tasks.length || new Set(scheduledIds).size !== tasks.length || scheduledIds.some(id => !byId.has(id))) throw new Error('批次需要完整且不重复地包含全部任务');
  document.querySelector('#summary').textContent = `${tasks.length} 个任务 · ${countEdges} 条依赖 · ${dependencyColumns.length} 个依赖层 · ${batches.length} 个批次${plan.capacity ? ` · 并发 ${plan.capacity}` : ''}${plan.revision ? ` · r${plan.revision}` : ''}`;
  let selectedTaskId = tasks[0].id;
  let layout = 'batches';
  const CARD_W = 164, CARD_H = 90, PITCH = 214, ROW = 126, PAD = 16;

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
    const top = 64 + longEdges.length * 9;
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
        : [start, [x + CARD_W + 16, start[1]], [x + CARD_W + 16, 39 + lane * 9], [tx - 24, 39 + lane * 9], [tx - 24, end[1]], end];
      return `<path data-from="${escape(edge.from)}" data-to="${escape(edge.to)}" d="${route(points)}" fill="none" stroke="#9eacc0" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#tip)"/>`;
    });
    board.innerHTML = `<svg width="${width}" height="${height}" aria-hidden="true"><defs><marker id="tip" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#7489ad"/></marker><marker id="tip-active" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="10" markerHeight="10" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#3657cb"/></marker></defs>${paths.join('')}</svg>${columns.map((column, i) => `<div class="layer-heading" style="left:${PAD + i * PITCH}px;width:${CARD_W}px"><span>${layout === 'dependencies' ? '依赖层' : '执行批次'} ${i + 1}</span><small>${column.length} 个任务</small></div>`).join('')}${tasks.map(task => {
      const [x, y] = point(task.id);
      return `<button type="button" class="node task-state-${statusOf(task.id)}" data-task="${escape(task.id)}" aria-pressed="false" aria-label="${escape(labels.get(task.id))} ${escape(task.title)} ${taskStates[statusOf(task.id)]}" style="left:${x}px;top:${y}px;width:${CARD_W}px;height:${CARD_H}px" title="${escape(task.title)}"><small class="node-id">${labels.get(task.id)}</small><strong>${escape(task.title)}</strong><small>${statusBadge(statusOf(task.id))}</small></button>`;
    }).join('')}`;
    setGraphZoom(graphZoom);
    select(selectedTaskId);
  }
  for (const button of document.querySelectorAll('[data-layout]')) {
    button.addEventListener('click', () => {
      layout = button.dataset.layout;
      for (const tab of document.querySelectorAll('[data-layout]')) tab.setAttribute('aria-pressed', String(tab === button));
      draw();
      canvas.scrollTo(0, 0);
    });
  }

  function select(id) {
    const task = byId.get(id);
    if (!task) return;
    selectedTaskId = id;
    const previous = deps(task), next = tasks.filter(t => deps(t).includes(id)).map(t => t.id);
    for (const button of view.querySelectorAll('[data-task]')) {
      button.setAttribute('aria-pressed', String(button.dataset.task === id));
      button.classList.toggle('related', previous.includes(button.dataset.task) || next.includes(button.dataset.task));
    }
    for (const path of board.querySelectorAll('path[data-from]')) {
      const active = path.dataset.from === id || path.dataset.to === id;
      path.setAttribute('stroke', active ? '#3657cb' : '#94a3b8');
      path.setAttribute('stroke-width', active ? '2.25' : '1.5');
      path.setAttribute('marker-end', active ? 'url(#tip-active)' : 'url(#tip)');
      path.style.opacity = active ? '1' : '0.45';
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
      ['交付产物', task.output ?? task.outputs],
      ['验收条件', task.acceptance],
      ...(effortText ? [['工作量估算', effortText]] : []),
    ];
    document.querySelector('#detail').innerHTML = `<h2>任务详情</h2><div class="reference">${labels.get(id)} · ${escape(id)}</div><h3>${escape(task.title)}</h3>${statusBadge(statusOf(id))}${execution[id]?.reason ? `<p class="state-reason state-${statusOf(id)}">${escape(execution[id].reason)}</p>` : ''}<dl>${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(text(value))}</dd></div>`).join('')}</dl>`;
  }
  view.addEventListener('click', event => {
    const button = event.target.closest('[data-task]');
    if (button) {
      select(button.dataset.task);
      if (button.classList.contains('task-item')) {
        const node = [...board.querySelectorAll('[data-task]')].find(node => node.dataset.task === button.dataset.task);
        if (node) {
          canvas.scrollLeft = Math.max(0, node.offsetLeft * graphZoom - canvas.clientWidth / 2 + 82 * graphZoom);
          canvas.scrollTop = Math.max(0, node.offsetTop * graphZoom - canvas.clientHeight / 2 + 45 * graphZoom);
        }
      }
    }
  });
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
const zoomValue = document.querySelector('#zoom-value');
let graphZoom = 1;
function setGraphZoom(value) {
  graphZoom = Math.min(2, Math.max(0.1, Math.floor(value * 100) / 100));
  const board = document.querySelector('#board');
  if (board) {
    board.style.zoom = String(graphZoom);
    board.classList.toggle('compact', graphZoom < 0.65);
  }
  zoomValue.value = `${Math.round(graphZoom * 100)}%`;
  document.querySelector('#zoom-out').disabled = graphZoom <= 0.1;
  document.querySelector('#zoom-in').disabled = graphZoom >= 2;
}
document.querySelector('#zoom-out').onclick = () => setGraphZoom(graphZoom - 0.1);
document.querySelector('#zoom-in').onclick = () => setGraphZoom(graphZoom + 0.1);
document.querySelector('#zoom-fit').onclick = () => {
  const board = document.querySelector('#board');
  if (!board) return;
  const style = getComputedStyle(canvas);
  const availableWidth = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const availableHeight = canvas.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  setGraphZoom(Math.min(1, availableWidth / board.offsetWidth, availableHeight / board.offsetHeight));
  canvas.scrollTo(0, 0);
};
document.querySelector('#zoom-reset').onclick = () => {
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
const graphLayout = document.querySelector('.graph-layout');
const detailBelow = matchMedia('(max-width:900px)');
for (const [buttonId, panelId, className, label] of [
  ['toggle-tasks', 'task-panel', 'tasks-collapsed', '任务列表'],
  ['toggle-detail', 'detail', 'detail-collapsed', '任务详情'],
]) {
  const button = document.getElementById(buttonId);
  const panel = document.getElementById(panelId);
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

show().catch(error => {
  document.querySelector('#summary').textContent = '图谱加载失败';
  canvas.innerHTML = `<p class="error-message">${escape(error.message)}。请返回需求工作台重新生成，或打开聊天样例。</p><a href="graph.html?sample=chat">打开聊天样例</a>`;
});

// Resize expanded side panes while reserving usable space for the graph.
for (const handle of document.querySelectorAll('[data-resize]')) {
  const left = handle.dataset.resize === 'tasks';
  const shell = handle.parentElement;
  const vertical = () => !left && detailBelow.matches;
  let drag = null;
  function updateOrientation() {
    const height = vertical();
    handle.setAttribute('aria-orientation', height ? 'horizontal' : 'vertical');
    handle.setAttribute('aria-label', `调整${left ? '任务列表' : '任务详情'}${height ? '高度' : '宽度'}`);
    handle.setAttribute('aria-valuemin', String(height ? 120 : left ? 180 : 200));
    handle.setAttribute('aria-valuemax', String(height ? 480 : left ? 420 : 440));
    handle.setAttribute('aria-valuenow', String(Math.round(height ? shell.getBoundingClientRect().height : shell.getBoundingClientRect().width)));
  }
  detailBelow.addEventListener('change', updateOrientation);
  updateOrientation();
  function setSize(value) {
    const height = vertical();
    const min = height ? 120 : left ? 180 : 200;
    const other = document.querySelector(left ? '.detail-shell' : '.task-shell');
    const beside = !detailBelow.matches;
    const room = height ? graphLayout.clientHeight - 400 - 8
      : graphLayout.clientWidth - (beside ? other.getBoundingClientRect().width : 0) - 280 - 16;
    const max = Math.max(min, Math.min(height ? 480 : left ? 420 : 440, room));
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
