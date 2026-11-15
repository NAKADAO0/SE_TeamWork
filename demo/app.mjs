import {decompose} from "./planner.mjs";
import {compile} from "./compiler.mjs";
const $ = id => document.getElementById(id);
let plan;
function reset() { plan=null; $("result").replaceChildren(); $("save").disabled=true; $("error").textContent=""; }
async function load() {
  reset();
  const sample=$("sample").value;
  try {
    const response=await fetch("./examples/"+sample+".json");
    if(!response.ok) throw Error("无法读取规格");
    const data=await response.json();
    if(sample===$("sample").value) $("spec").value=JSON.stringify(data,null,2);
  } catch(e) { $("error").textContent=e.message; }
}
$("sample").onchange=load;
$("capacity").oninput=$("spec").oninput=reset;
$("run").onclick=()=>{
  reset();
  try {
    const spec=JSON.parse($("spec").value), tasks=decompose(spec);
    const capacity=Number($("capacity").value), batches=compile(tasks,capacity);
    plan={mode:"offline_rule",spec,capacity,tasks,batches};
    sessionStorage.setItem("se.plan",JSON.stringify(plan));
    for(const batch of batches){
      const row=document.createElement("li");
      row.textContent="本批可并行"; const list=document.createElement("ul");
      for(const id of batch){
        const t=tasks.find(x=>x.id===id), item=document.createElement("li");
        item.textContent=id+" "+t.title+" · "+t.role+" · "+(t.depends_on.join(", ")||"设计规格")+" → "+id+" · 验收："+t.acceptance;
        list.append(item);
      }
      row.append(list); $("result").append(row);
    }
    $("save").disabled=false;
  } catch(e){ $("error").textContent=e.message; }
};
$("save").onclick=()=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(plan,null,2)],{type:"application/json"}));
  const link=document.createElement("a");link.href=url;link.download="plan.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
const selected = new URLSearchParams(location.search).get("sample");
if (["chat", "community"].includes(selected)) $("sample").value = selected;
const previous=JSON.parse(sessionStorage.getItem("se.plan") || "null");
if (!selected && previous) {
  $("spec").value=JSON.stringify(previous.spec,null,2);
  $("capacity").value=previous.capacity;
  $("run").click();
} else load();
