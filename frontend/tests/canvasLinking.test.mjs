import assert from "node:assert/strict";
import test from "node:test";
import { canLink, linkTasks, unlinkTasks } from "../src/features/workflow-graph/canvasLinking.js";

const tasks = () => [
  { id: "A", depends_on: [] },
  { id: "B", depends_on: [{ task_id: "A", reason: "A 产出 B 的输入" }] },
  { id: "C", depends_on: [] },
];

test("自依赖与重复连线被拒绝", () => {
  assert.equal(canLink(tasks(), "A", "A").code, "SELF_DEPENDENCY");
  assert.equal(canLink(tasks(), "A", "B").code, "DUPLICATE_EDGE");
});

test("会形成环的连线被拒绝并给出路径", () => {
  const verdict = canLink(tasks(), "B", "A");
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "DEPENDENCY_CYCLE");
  assert.deepEqual(verdict.path, ["B", "A", "B"]);
});

test("合法连线写入 depends_on 且不改动原数组", () => {
  const before = tasks();
  const result = linkTasks(before, "A", "C");
  assert.equal(result.ok, true);
  assert.equal(result.tasks.find((task) => task.id === "C").depends_on.length, 1);
  assert.equal(before.find((task) => task.id === "C").depends_on.length, 0);
});

test("删除依赖只移除指定的一条", () => {
  const source = [
    { id: "A", depends_on: [] },
    { id: "B", depends_on: [] },
    { id: "C", depends_on: [{ task_id: "A" }, { task_id: "B" }] },
  ];
  const next = unlinkTasks(source, "A", "C");
  const deps = next.find((task) => task.id === "C").depends_on.map((dep) => dep.task_id ?? dep);
  assert.deepEqual(deps, ["B"]);
});
