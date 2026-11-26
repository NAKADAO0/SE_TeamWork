import { createApi } from "../shared/api.js";
import { mountAgentExecution } from "../features/orchestration/agentExecution.js";
import { mountReview } from "../features/review/reviewPanel.js";
import { makeWorkstreams } from "../features/orchestration/workstreams.js";
import { mountLegacyGraph } from "./legacyGraph.js";
import { mountLegacyPackages } from "./legacyPackages.js";

export function mountExecutionWorkspace(root) {
  root.innerHTML = `<div class="execution-summary" role="status"></div><div class="execution-toolbar"><div class="execution-switch" role="group" aria-label="执行视图"><button type="button" data-view="packages">项目工作包</button><button type="button" data-view="graph">任务图谱</button></div><div class="execution-launch"><button type="button" data-launch="demo">运行规则演示</button><button type="button" class="primary" data-launch="live">开始执行</button></div><button type="button" data-expand-execution aria-expanded="false">展开画布</button></div><div class="legacy-packages-host"></div><div class="legacy-graph-host" hidden></div><section class="execution-agent" aria-label="Agent 执行控制"></section><section class="execution-review" aria-label="验证与交付"></section>`;
  const el=s=>root.querySelector(s), api=createApi({mode:"http",base:""});
  let context,run,view="packages";
  const agents=mountAgentExecution(el('.execution-agent'),next=>{run=next;if(context) draw();});
  const review=mountReview(el('.execution-review'));
  const scopeForTask=task=>makeWorkstreams(context.plan).groups.find(g=>g.task_ids.includes(task.id));
  const graph=mountLegacyGraph(el('.legacy-graph-host'), task=>{if(context) agents.setScope(scopeForTask(task));});
  const packages=mountLegacyPackages(el('.legacy-packages-host'),{
    onSelect:group=>agents.setScope(group),
    onTask:id=>{view="graph";draw();graph.select(id);},
    onGraph:()=>{view="graph";draw();},
    onDiscuss:()=>{
      if(!run){el('.execution-launch').scrollIntoView({behavior:"smooth",block:"center"});el('[data-launch="live"]').focus();return;}
      el('.execution-agent').scrollIntoView({behavior:"smooth",block:"start"});el('#agent-note').focus();
    },
  });
  function collapse(){root.classList.remove('execution-expanded');el('[data-expand-execution]').setAttribute('aria-expanded','false');el('[data-expand-execution]').textContent='展开画布';}
  document.addEventListener('keydown',e=>{if(e.key==='Escape')collapse();});
  function draw(){
    const {plan,execution,stage,readonly,confirmed}=context;
    const delivery=stage==='delivery';
    if(execution.compiled === false) view='packages';
    el('.execution-toolbar').hidden=delivery;
    el('.legacy-packages-host').hidden=delivery || view!=='packages';
    el('.legacy-graph-host').hidden=delivery || view!=='graph';
    el('.execution-agent').hidden=delivery || readonly || !confirmed;
    el('.execution-review').hidden=!delivery || readonly || !confirmed;
    el('.execution-launch').hidden=delivery || readonly || !confirmed || Boolean(run&&run.status!=='done');
    root.querySelectorAll('[data-launch]').forEach(b=>{b.disabled=el(`[data-run="${b.dataset.launch}"]`).disabled;});
    root.querySelectorAll('[data-view]').forEach(b=>{
      b.setAttribute('aria-pressed',String(b.dataset.view===view));
      b.disabled=b.dataset.view==='graph' && execution.compiled===false;
    });
    el('.execution-summary').textContent=readonly?'历史只读 · 不加载当前执行状态，不提供执行操作。':!confirmed?'请先确认计划，再启动执行。':delivery?'提交开发产物与代码差异，完成自动审查和人工审核。':`${plan.tasks.length} 个任务 · ${execution.edges.length} 条依赖 · ${execution.batches.length} 个批次 · ${run?({running:'执行中',paused:'已暂停',failed:'执行失败',done:'批次已完成'}[run.status]??run.status):'计划已确认，尚未启动开发'}`;
    if(!delivery){
      if(view==='packages')packages.update(plan,execution,run);
      else graph.update(plan,execution,run);
    }
  }
  root.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b||!context)return;
    if(b.dataset.view){view=b.dataset.view;draw();}
    if(b.dataset.launch){el(`[data-run="${b.dataset.launch}"]`).click();root.querySelectorAll('[data-launch]').forEach(b=>b.disabled=true);}
    if(b.hasAttribute('data-expand-execution')){const expanded=root.classList.toggle('execution-expanded');b.setAttribute('aria-expanded',String(expanded));b.textContent=expanded?'收起画布':'展开画布';}
  });
  return {update(next){
    const key=next?`${next.plan.plan_id}:${next.plan.revision}:${next.readonly}`:null;
    if(key!==context?.key){run=null;view='packages';packages.reset();}
    context=next?{...next,key}:null;
    root.hidden=!context;
    if(!context||context.stage!=='execution')collapse();
    agents.update(api,context?.confirmed&&!context.readonly?context.plan:null);
    review.update(api,context?.confirmed&&!context.readonly&&context.stage==='delivery'?context.plan:null);
    if(context)draw();
  }};
}
