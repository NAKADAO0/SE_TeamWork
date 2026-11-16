import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { schedule } from "../src/shared/fixtureSchedule.js";
import { precheck } from "../src/shared/precheck.js";

const planFixture = JSON.parse(
  await readFile(new URL("../fixtures/taskplan-chat.json", import.meta.url), "utf8"),
);
const specDraft = JSON.parse(
  await readFile(new URL("../fixtures/spec-chat.json", import.meta.url), "utf8"),
);
const clone = (value) => JSON.parse(JSON.stringify(value));
const config = { max_concurrency: 2, role_capacity: { backend: 1, frontend: 1, qa: 1 } };

test("聊天样例按固定配置排出 6 个批次，且覆盖全部任务", () => {
  const result = schedule(clone(planFixture.tasks), config);
  assert.equal(result.compiled, true);
  const batches = result.batches.map((batch) => batch.tasks);
  assert.deepEqual(batches[0], ["C1"]);
  assert.deepEqual(batches[1], ["C2", "C3"]);
  assert.deepEqual(batches[2], ["C4", "C6"]);
  assert.equal(batches.length, 6);
  assert.equal(result.edges.length, 11);
  assert.deepEqual(result.nodes.sort(), planFixture.tasks.map((task) => task.id).sort());
});

test("同一输入重复编排结果完全一致", () => {
  const first = schedule(clone(planFixture.tasks), config);
  const second = schedule(clone(planFixture.tasks), config);
  assert.deepEqual(first, second);
});

test("提高 qa 容量只改变批次，不改变依赖边", () => {
  const tasks = [
    { id: "A", role: "backend", priority: 1, depends_on: [] },
    { id: "B", role: "frontend", priority: 1, depends_on: [] },
    { id: "C", role: "qa", priority: 1, depends_on: [{ task_id: "A" }] },
    { id: "D", role: "qa", priority: 1, depends_on: [{ task_id: "B" }] },
    { id: "E", role: "qa", priority: 1, depends_on: [{ task_id: "C" }, { task_id: "D" }] },
  ];
  const narrow = schedule(clone(tasks), {
    max_concurrency: 3,
    role_capacity: { backend: 1, frontend: 1, qa: 1 },
  });
  const wide = schedule(clone(tasks), {
    max_concurrency: 3,
    role_capacity: { backend: 1, frontend: 1, qa: 2 },
  });
  assert.equal(narrow.batches.length, 4);
  assert.equal(wide.batches.length, 3);
  assert.deepEqual(narrow.edges, wide.edges);
});

test("有环时返回阻塞而不是抛错", () => {
  const result = schedule(
    [
      { id: "A", role: "qa", priority: 1, depends_on: [{ task_id: "B" }] },
      { id: "B", role: "qa", priority: 1, depends_on: [{ task_id: "A" }] },
    ],
    config,
  );
  assert.equal(result.compiled, false);
  assert.equal(result.blocked.length, 2);
  assert.match(result.blocked[0].reason, /等待前置/);
});

test("非法并发数直接拒绝", () => {
  assert.throws(() => schedule(clone(planFixture.tasks), { max_concurrency: 0, role_capacity: {} }));
});

test("聊天样例通过预检，删除验证任务后报出覆盖错误", () => {
  const clean = precheck({ plan: clone(planFixture), spec: clone(specDraft) });
  assert.deepEqual(clean.blocking, []);

  const broken = clone(planFixture);
  broken.tasks = broken.tasks.filter((task) => task.id !== "C7");
  const report = precheck({ plan: broken, spec: clone(specDraft) });
  const codes = report.blocking.map((item) => item.code);
  assert.ok(codes.includes("REQUIREMENT_WITHOUT_VERIFICATION"));
  const issue = report.blocking.find((item) => item.code === "REQUIREMENT_WITHOUT_VERIFICATION");
  assert.equal(issue.object_id, "R-MSG-02");
});

test("人为制造环时给出真实闭合路径", () => {
  const broken = clone(planFixture);
  broken.tasks.find((task) => task.id === "C1").depends_on = [{ task_id: "C8", reason: "人为制造" }];
  const report = precheck({ plan: broken, spec: clone(specDraft) });
  const issue = report.blocking.find((item) => item.code === "DEPENDENCY_CYCLE");
  assert.ok(issue);
  assert.ok(issue.related_ids.length >= 3);
  assert.equal(issue.related_ids[0], issue.related_ids.at(-1));
});

test("输入引用不存在的产物会被拦下", () => {
  const broken = clone(planFixture);
  broken.tasks.find((task) => task.id === "C2").inputs = [
    { source: "task_output", ref: "C99/O-NOT-EXIST", type: "implementation" },
  ];
  const report = precheck({ plan: broken, spec: clone(specDraft) });
  assert.ok(report.blocking.some((item) => item.code === "UNRESOLVED_INPUT"));
});
