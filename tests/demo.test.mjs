import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {decompose} from "../demo/planner.js";
import {compile} from "../demo/compiler.js";
for(const product of ["chat","community"]) test(product,()=>{
  const spec=JSON.parse(readFileSync(new URL("../examples/"+product+".json",import.meta.url)));
  const tasks=decompose(spec);
  assert.deepEqual(compile(tasks,2),[["R1-I","R2-I"],["R1-V","R2-V"]]);
  assert.equal(compile(tasks,1).length,4);
  assert.equal(tasks[1].acceptance,spec.requirements[0].acceptance);
});
test("reject invalid graphs",()=>{
  assert.throws(()=>compile([{id:"a",depends_on:["a"]}],1),/依赖环/);
  assert.throws(()=>compile([{id:"a",depends_on:["b"]}],1),/缺少/);
  assert.throws(()=>compile([],0),/正整数/);
});
