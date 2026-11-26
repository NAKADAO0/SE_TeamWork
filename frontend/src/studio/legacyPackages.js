import { escapeHtml as h } from "../shared/dom.js";
import { mountWorkstreamCanvas } from "../features/orchestration/workstreamCanvas.js";
import { branchOverview } from "../features/orchestration/branchOverview.js";

// Reuse the original work-package cards, connections and expandable detail content.
export function mountLegacyPackages(host, {onSelect=()=>{}, onTask=()=>{}, onDiscuss=()=>{}, onGraph=()=>{}} = {}) {
  const scope=host.attachShadow({mode:"open"});
  scope.innerHTML=`<link rel="stylesheet" href="/frontend/src/studio/workPackages.css"><link rel="stylesheet" href="/frontend/src/studio/legacyPackages.css"><div class="package-layout"><div class="package-main"><div class="orch-map wp-map"><div class="orch-map-head"><strong>项目工作流</strong><span data-workstream-caption></span></div><div class="wp-empty" data-workstream-empty><strong>暂无工作包</strong></div><div class="wp-board" data-workstream-board hidden></div><details class="wp-edge-info" data-workstream-handoffs hidden><summary>查看工作包之间的交接</summary><p>箭头由小任务依赖汇总。双向交接表示不同小任务之间的交接；实际顺序以小任务图谱为准。</p><ul class="wp-handoffs"></ul></details></div><div class="package-metrics"></div><section class="package-branches"></section></div><section class="package-detail" hidden><header><div><small data-package-id></small><h2 data-package-title></h2></div><button type="button" data-close aria-label="关闭工作包详情">×</button></header><div class="package-detail-body"><div data-workstream-detail></div></div></section></div>`;
  const el=s=>scope.querySelector(s);
  let plan, execution, key, selected;
  const packages=mountWorkstreamCanvas(scope, next => select(next));
  function select(next) {
    selected=next;
    const group=packages.get(next);
    el('.package-detail').hidden=!group;
    el('.package-layout').classList.toggle('has-detail',Boolean(group));
    if(group) {
      el('[data-package-id]').textContent=`项目工作包 · ${group.id}`;
      el('[data-package-title]').textContent=group.title;
    }
    packages.select(next);
    onSelect(group ?? null);
    for(const d of scope.querySelectorAll('[data-child-task]')) {
      if(!d.querySelector('[data-open-task]')) d.insertAdjacentHTML('beforeend',`<button type="button" data-open-task="${h(d.dataset.childTask)}">在任务图谱中查看 →</button>`);
    }
  }
  scope.addEventListener('click',e=>{
    if(e.target.closest('[data-close]')) select(null);
    if(e.target.closest('[data-package-discuss]')) onDiscuss();
    const task=e.target.closest('[data-open-task]'); if(task) onTask(task.dataset.openTask);
    if(e.target.closest('[data-open-graph]')) onGraph();
  });
  return {
    update(next, compiled, run) {
      execution=compiled;
      const signature=JSON.stringify(next);
      if(signature!==key) { plan=next; key=signature; }
      packages.update({plan, phase:"confirmed"},run);
      select(selected);
      const {handoffs,joins,parallel}=branchOverview(plan.tasks,execution.batches);
      el('.package-metrics').innerHTML=[['开发任务',plan.tasks.length],['依赖关系',execution.edges.length],['执行批次',execution.batches.length],['校验错误',0]].map(([label,value])=>`<div><span>${label}</span><strong>${value}</strong></div>`).join('');
      const join=joins[0];
      const brief=t=>`<div class="branch-task"><small>${h(t.role)} · ${h(t.id)}</small><strong>${h(t.title)}</strong></div>`;
      el('.package-branches').innerHTML=`<header><div><h3>任务交叉路径</h3><p>${handoffs.length} 条跨角色依赖 · ${joins.length} 个分支汇合 · ${parallel.length} 个并行批次</p></div><button type="button" data-open-graph>查看编排细节 →</button></header>${join ? `<div class="branch-join"><div>${join.inputs.slice(0,3).map(brief).join('')}</div><span>→</span>${brief(join.task)}</div>` : '<p>当前没有独立分支汇合。</p>'}`;
    },
    reset(){selected=null; select(null);},
  };
}
