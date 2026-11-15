const canvas=document.querySelector("#canvas");
async function show(){
  const key=new URLSearchParams(location.search).get("sample");
  const stored=sessionStorage.getItem("se.plan");
  const preview=key==="chat" || key==="community" || !stored;
  const sample=key==="community"?"community":"chat";
  const plan=preview ? await (await fetch("examples/"+sample+"-plan.json")).json() : JSON.parse(stored);
  const {tasks,batches,spec}=plan;
  document.querySelector("#summary").textContent=(preview?"已完成的编排案例 · ":"当前计划 · ")+spec.product+"　/　"+tasks.length+" 个任务　/　"+batches.length+" 个批次";
  if(preview){
    const overview=document.createElement("article");
    const scope=document.createElement("p");scope.textContent=spec.scope;overview.append(scope);
    for(const r of spec.requirements){
      const line=document.createElement("p");line.textContent=r.id+" · "+r.goal+" ｜ 验收："+r.acceptance;overview.append(line);
    }
    const download=document.createElement("a");download.href="examples/"+sample+"-plan.json";download.download=sample+"-plan.json";download.textContent="下载完整计划 JSON";overview.append(download);const info=document.createElement("details");
    const heading=document.createElement("summary");heading.textContent="案例规格与下载";info.append(heading,overview);canvas.before(info);
  }
  const board=document.createElement("div");
  board.id="board";
  canvas.append(board);
  const positions=new Map();
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
  svg.setAttribute("width","100%");svg.setAttribute("height","100%");board.append(svg);
  svg.innerHTML='<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z" fill="#7489ad"/></marker></defs>';
  const labels=[], nodes=[], edges=[];
  batches.forEach((batch,col)=>{
    const label=document.createElement("b");label.className="batch";label.style.left=col*244+"px";label.textContent="批次 "+(col+1);board.append(label);labels.push(label);
    batch.forEach((id,row)=>positions.set(id,[col,row]));
  });
  for(const t of tasks){
    const [x,y]=positions.get(t.id);
    for(const dep of t.depends_on){
      const [px,py]=positions.get(dep);
      const line=document.createElementNS(svg.namespaceURI,"path");
      line.setAttribute("d",`M ${px+180} ${py+33} C ${px+214} ${py+33}, ${x-32} ${y+33}, ${x} ${y+33}`);
      line.setAttribute("fill","none");line.setAttribute("stroke","#7489ad");line.setAttribute("marker-end","url(#tip)");svg.append(line);edges.push({line,from:dep,to:t.id});
    }
    const button=document.createElement("button");button.className="node "+t.role;button.style.left=x+"px";button.style.top=y+"px";
    const title=document.createElement("strong"),meta=document.createElement("small");title.textContent=t.title;meta.textContent=t.id+" · "+t.role;button.append(title,meta);board.append(button);nodes.push({button,id:t.id});
    button.onclick=()=>{
      board.querySelector('[aria-pressed=true]')?.removeAttribute("aria-pressed");button.setAttribute("aria-pressed","true");
      document.querySelector("#detail").textContent=t.title+"　|　需求："+t.source+"　|　交付："+(t.output||t.title)+"　|　前置："+(t.depends_on.join("、")||"设计规格")+"　|　验收："+t.acceptance;
    };
  }
  function layout(){
    const columns=batches.length, rows=Math.max(1,...batches.map(batch=>batch.length));
    const width=Math.max(canvas.clientWidth-48,columns*216-36);
    const height=Math.max(canvas.clientHeight-48,rows*110+40);
    const pitch=(width+36)/columns, card=pitch-36;
    const rowHeight=(height-40)/rows;
    board.style.width=width+"px";board.style.height=height+"px";
    const point=id=>{
      const [col,row]=positions.get(id);
      return [col*pitch,40+(row+(rows-batches[col].length)/2+.5)*rowHeight-38];
    };
    labels.forEach((label,col)=>label.style.left=col*pitch+"px");
    for(const {button,id} of nodes){
      const [x,y]=point(id);
      Object.assign(button.style,{left:x+"px",top:y+"px",width:card+"px",height:"76px"});
    }
    for(const {line,from,to} of edges){
      const [x,y]=point(from),[tx,ty]=point(to);
      line.setAttribute("d",`M ${x+card} ${y+38} C ${x+card+20} ${y+38}, ${tx-20} ${ty+38}, ${tx} ${ty+38}`);
    }
  }
  new ResizeObserver(layout).observe(canvas);
}
const view=document.querySelector("#graph-view"), toggle=document.querySelector("#fullscreen");
function updateToggle(){toggle.textContent=document.fullscreenElement || view.classList.contains("expanded")?"退出全屏":"全屏显示";}
toggle.onclick=async()=>{
  if(document.fullscreenElement) await document.exitFullscreen();
  else if(view.classList.contains("expanded")) view.classList.remove("expanded");
  else {try {await view.requestFullscreen();} catch {view.classList.add("expanded");}}
  updateToggle();
};
document.addEventListener("fullscreenchange",updateToggle);
document.addEventListener("keydown",e=>{if(e.key==="Escape"){view.classList.remove("expanded");updateToggle();}});
show().catch(()=>canvas.textContent="图谱加载失败，请返回需求工作台重新生成。");
