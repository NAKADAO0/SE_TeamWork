import { sampleProgress } from './graph-progress.js';
const canvas = document.querySelector('#canvas');
const view = document.querySelector('#graph-view');
const toggle = document.querySelector('#fullscreen');
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const text = value => Array.isArray(value) ? value.join('；') : value || '未提供';
const roles = { backend: '后端', frontend: '前端', qa: '测试' };

async function show() {
  const key = new URLSearchParams(location.search).get('sample');
  const stored = sessionStorage.getItem('se.plan');
  const preview = key === 'chat' || key === 'community' || !stored;
  const sample = key === 'community' ? 'community' : 'chat';
  const response = preview ? await fetch(`examples/${sample}-plan.json`) : null;
  if (response && !response.ok) throw new Error('样例读取失败');
  const plan = preview ? await response.json() : JSON.parse(stored);
  const { tasks, batches, spec } = plan;
  if (!Array.isArray(tasks) || !tasks.length || !Array.isArray(batches)) throw new Error('计划缺少任务或批次');
  const metadata = preview ? sampleProgress[sample] : null;
  const done = new Set(metadata?.done ?? []);
  const phases = metadata?.phases ?? [{ title: '当前计划（阶段待对齐）', tasks: tasks.map(t => t.id) }];
  const byId = new Map(tasks.map(task => [task.id, task]));
  const deps = task => (task.depends_on ?? []).map(dep => typeof dep === 'string' ? dep : dep.task_id);
  const labels = new Map();
  phases.forEach((phase, pi) => phase.tasks.forEach((id, ti) => labels.set(id, `Task ${pi + 1}.${ti + 1}`)));
  const countEdges = tasks.reduce((sum, task) => sum + deps(task).length, 0);
  document.querySelector('#summary').textContent = `${preview ? '样例预览' : '当前本地计划'} · ${typeof spec.product === 'object' ? spec.product.name : spec.product} · ${tasks.length} 个任务 · ${countEdges} 条依赖 · ${batches.length} 个批次${plan.revision ? ` · 修订 r${plan.revision}` : ''}`;
  // Legacy generated plans remain in the legacy workbench until HTTP integration is ready.
  if (!preview) document.querySelector('#return-link').href = 'workspace.html';
  const percent = Math.round(done.size / tasks.length * 100);
  document.querySelector('#progress').innerHTML = `<div class="progress-heading"><h2>总开发进度</h2><span class="simulation">${preview ? '模拟进度' : '暂无执行数据'}</span></div><div class="total-value">${preview ? `${done.size}/${tasks.length}` : '—'} <small>${preview ? `已完成 · ${percent}%` : '未接入任务执行状态'}</small></div>${preview ? `<progress value="${done.size}" max="${tasks.length}" aria-label="模拟总开发进度"></progress>` : ''}`;
  const list = document.querySelector('#task-list');
  list.innerHTML = phases.map((phase, pi) => {
    const ids = phase.tasks.filter(id => byId.has(id));
    const complete = ids.filter(id => done.has(id)).length;
    return `<details class="phase-group" open><summary>第${pi + 1}阶段 · ${escape(phase.title)}<span>${preview ? complete : '—'}/${ids.length}</span></summary>${ids.map(id => `<button type="button" class="task-item" data-task="${escape(id)}" aria-pressed="false"><span class="status-mark ${done.has(id) ? 'done' : ''}" aria-label="${done.has(id) ? '模拟已完成' : preview ? '模拟未完成' : '状态未知'}">${done.has(id) ? '✓' : '○'}</span><span><small>${labels.get(id)}</small><b>${escape(byId.get(id).title)}</b></span></button>`).join('')}</details>`;
  }).join('');

  const board = document.createElement('div');
  board.id = 'board';
  canvas.append(board);
  const positions = new Map();
  batches.forEach((batch, col) => (Array.isArray(batch) ? batch : batch.tasks).forEach((id, row) => positions.set(id, [col, row])));
  if (tasks.some(task => !positions.has(task.id) || deps(task).some(dep => !positions.has(dep)))) throw new Error('批次或依赖引用了不存在的任务');
  const width = Math.max(1, batches.length) * 210;
  const rows = Math.max(1, ...batches.map(batch => (Array.isArray(batch) ? batch : batch.tasks).length));
  const height = Math.max(320, rows * 128 + 70);
  board.style.width = `${width}px`;
  board.style.height = `${height}px`;
  const point = id => { const [col, row] = positions.get(id); return [col * 210, row * 128 + 54]; };
  const paths = tasks.flatMap(task => deps(task).map(dep => {
    const [x, y] = point(dep), [tx, ty] = point(task.id);
    return `<path data-from="${escape(dep)}" data-to="${escape(task.id)}" d="M ${x + 174} ${y + 43} C ${x + 194} ${y + 43}, ${tx - 20} ${ty + 43}, ${tx} ${ty + 43}" fill="none" stroke="#a2afc4" stroke-width="1.5" marker-end="url(#tip)"/>`;
  }));
  board.innerHTML = `<svg width="${width}" height="${height}" aria-hidden="true"><defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#7489ad"/></marker></defs>${paths.join('')}</svg>${batches.map((_, i) => `<b class="batch" style="left:${i * 210}px">执行批次 ${i + 1}</b>`).join('')}${tasks.map(task => {
    const [x, y] = point(task.id);
    return `<button type="button" class="node ${done.has(task.id) ? 'completed' : ''}" data-task="${escape(task.id)}" aria-pressed="false" style="left:${x}px;top:${y}px;width:174px" title="${escape(task.title)}"><small>${labels.get(task.id)} · ${escape(roles[task.role] ?? task.role)}</small><strong>${escape(task.title)}</strong><small>${done.has(task.id) ? '✓ 模拟已完成' : preview ? '○ 模拟未完成' : '状态未知'}</small></button>`;
  }).join('')}`;

  function select(id) {
    const task = byId.get(id);
    if (!task) return;
    const previous = deps(task), next = tasks.filter(t => deps(t).includes(id)).map(t => t.id);
    for (const button of view.querySelectorAll('[data-task]')) {
      button.setAttribute('aria-pressed', String(button.dataset.task === id));
      button.classList.toggle('related', previous.includes(button.dataset.task) || next.includes(button.dataset.task));
    }
    for (const path of board.querySelectorAll('path[data-from]')) {
      const active = path.dataset.from === id || path.dataset.to === id;
      path.setAttribute('stroke', active ? '#3657cb' : '#a2afc4');
      path.setAttribute('stroke-width', active ? '2.5' : '1.5');
    }
    const names = ids => ids.map(ref => `${labels.get(ref)} ${byId.get(ref)?.title ?? ref}`).join('；') || '无';
    const fields = [ ['角色', roles[task.role] ?? task.role], ['状态', preview ? done.has(id) ? '模拟已完成' : '模拟未完成' : '未知（未接入执行数据）'], ['所属需求', task.requirement_ids ?? task.source], ['前置任务', names(previous)], ['后续任务', names(next)], ['交付产物', task.output ?? task.outputs], ['验收条件', task.acceptance] ];
    document.querySelector('#detail').innerHTML = `<h2>任务详情</h2><div class="reference">${labels.get(id)} · ${escape(id)}</div><h3>${escape(task.title)}</h3><dl>${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${escape(text(value))}</dd></div>`).join('')}</dl>`;
  }
  view.addEventListener('click', event => {
    const button = event.target.closest('[data-task]');
    if (button) {
      select(button.dataset.task);
      if (button.classList.contains('task-item')) {
        const node = [...board.querySelectorAll('[data-task]')].find(node => node.dataset.task === button.dataset.task);
        if (node) canvas.scrollLeft = Math.max(0, node.offsetLeft - canvas.clientWidth / 2 + 87);
      }
    }
  });
  select(tasks[0].id);
}
function updateToggle() { toggle.textContent = document.fullscreenElement || view.classList.contains('expanded') ? '退出全屏' : '全屏显示'; }
toggle.onclick = async () => {
  if (document.fullscreenElement) await document.exitFullscreen();
  else if (view.classList.contains('expanded')) view.classList.remove('expanded');
  else { try { await view.requestFullscreen(); } catch { view.classList.add('expanded'); } }
  updateToggle();
};
document.addEventListener('fullscreenchange', updateToggle);
document.addEventListener('keydown', event => { if (event.key === 'Escape') { view.classList.remove('expanded'); updateToggle(); } });
show().catch(error => {
  document.querySelector('#summary').textContent = '图谱加载失败';
  canvas.innerHTML = `<p class="error-message">${escape(error.message)}。请返回需求工作台重新生成，或打开聊天样例。</p><a href="graph.html?sample=chat">打开聊天样例</a>`;
});
