import {decompose} from "./planner.mjs";
import {compile} from "./compiler.mjs";
const canvas=document.querySelector("#canvas");
async function show(){
  let plan=JSON.parse(sessionStorage.getItem("se.plan") || "null");
  const preview=!plan;
  if(preview){
    const spec=await (await fetch("examples/chat.json")).json();
    const tasks=decompose(spec);
    plan={spec,tasks,batches:compile(tasks,2)};
  }
  const {tasks,batches,spec}=plan;
  document.querySelector("#summary").textContent=(preview?"样例预览 · ":"当前计划 · ")+spec.product+"　/　"+tasks.length+" 个任务　/　"+batches.length+" 个批次";
  const board=document.createElement("div");
  board.id="board";
  board.style.width=(batches.length*244-64)+"px";
  board.style.height=(Math.max(...batches.map(b=>b.length))*94+32)+"px";
  canvas.append(board);
  const positions=new Map();
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.setAttribute("width","100%");svg.setAttribute("height","100%");board.append(svg);
  svg.innerHTML='<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#7489ad"/></marker></defs>';
  batches.forEach((batch,col)=>{
    const label=document.createElement("b");label.className="batch";label.style.left=col*244+"px";label.textContent="批次 "+(col+1);board.append(label);
    batch.forEach((id,row)=>positions.set(id,[col*244,32+row*94]));
  });
  for(const t of tasks){
    const [x,y]=positions.get(t.id);
    for(const dep of t.depends_on){
      const [px,py]=positions.get(dep);
      const line=document.createElementNS(svg.namespaceURI,"path");
      line.setAttribute("d",`M ${px+180} ${py+33} C ${px+214} ${py+33}, ${x-32} ${y+33}, ${x} ${y+33}`);
      line.setAttribute("fill","none");line.setAttribute("stroke","#7489ad");line.setAttribute("marker-end","url(#tip)");svg.append(line);
    }
    const button=document.createElement("button");button.className="node "+t.role;button.style.left=x+"px";button.style.top=y+"px";
    const title=document.createElement("strong"),meta=document.createElement("small");title.textContent=t.title;meta.textContent=t.id+" · "+t.role;button.append(title,meta);board.append(button);
    button.onclick=()=>{
      board.querySelector('[aria-pressed=true]')?.removeAttribute("aria-pressed");button.setAttribute("aria-pressed","true");
      document.querySelector("#detail").textContent=t.title+"　|　前置："+(t.depends_on.join("、")||"设计规格")+"　|　验收："+t.acceptance;
    };
  }
}
show().catch(()=>canvas.textContent="图谱加载失败，请返回需求工作台重新生成。");
