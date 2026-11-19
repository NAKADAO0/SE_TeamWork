import assert from "node:assert/strict";
import test from "node:test";
import { makeWorkstreams, workstreamProgress } from "../src/features/orchestration/workstreams.js";

test("package handoffs come from child dependencies, including two-way exchanges", () => {
  const plan = { workstreams: [
    {id:"W1",title:"服务",task_ids:["T1","T3"]}, {id:"W2",title:"界面",task_ids:["T2"]},
  ], tasks:[
    {id:"T1"}, {id:"T2",depends_on:[{task_id:"T1",reason:"使用服务契约"}]},
    {id:"T3",depends_on:["T2"]},
  ] };
  const view=makeWorkstreams(plan);
  assert.equal(view.origin,"ai");
  assert.deepEqual(view.edges.map(({from,to})=>[from,to]),[["W1","W2"],["W2","W1"]]);
  const progress=workstreamProgress(view.groups[0],{
    cursor:1,status:"paused",results:{T1:{}},execution:{batches:[["T1"],["T3"]]}
  });
  assert.equal(progress.complete,1);
  assert.equal(progress.total,2);
  assert.equal(progress.tasks.find(task=>task.id==="T3").status,"paused");
  assert.equal(makeWorkstreams({...plan,workstreams:[]}).origin,"derived");
});
