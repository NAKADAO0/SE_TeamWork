import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { branchOverview } from "../src/features/orchestration/branchOverview.js";

test("only independent direct inputs count as a branch intersection", () => {
  const tasks = [
    { id:"A", role:"backend", depends_on:[] },
    { id:"B", role:"frontend", depends_on:[] },
    { id:"C", role:"qa", depends_on:[{task_id:"A", reason:"服务产物"}, "B"] },
    { id:"D", role:"qa", depends_on:["A", "C"] },
  ];
  const view = branchOverview(tasks, [["A","B"], {tasks:["C"]}, ["D"]]);
  assert.deepEqual(view.joins.map(({task,inputs}) => [task.id,inputs.map(t=>t.id)]), [["C",["A","B"]]]);
  assert.equal(view.handoffs.length, 3);
  assert.deepEqual(view.parallel, [{number:1, ids:["A","B"]}]);
});

test("the shipped chat and community samples contain real cross-role dependencies and convergence", async () => {
  for (const name of ["chat", "community"]) {
    const plan = JSON.parse(await readFile(new URL(`../../examples/${name}-plan.json`, import.meta.url), "utf8"));
    const view = branchOverview(plan.tasks);
    assert.ok(view.handoffs.length, `${name}: cross-role handoff`);
    assert.ok(view.joins.length, `${name}: independent branches converge`);
  }
});
