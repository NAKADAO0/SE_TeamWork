import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { PHASE, initialState, reduce } from "../src/shared/reducer.js";

const specDraft = JSON.parse(
  await readFile(new URL("../fixtures/spec-chat.json", import.meta.url), "utf8"),
);
const planFixture = JSON.parse(
  await readFile(new URL("../fixtures/taskplan-chat.json", import.meta.url), "utf8"),
);

const clone = (value) => JSON.parse(JSON.stringify(value));

function confirmedPlan() {
  let state = reduce(initialState(), { type: "SPEC_LOADED", draft: clone(specDraft) });
  state = reduce(state, {
    type: "SPEC_CONFIRMED",
    ref: { spec_id: "SPEC-CHAT", version: 1 },
  });
  state = reduce(state, { type: "JOB_STARTED", jobId: "JOB-1" });
  return reduce(state, { type: "JOB_SUCCEEDED", plan: clone(planFixture) });
}

function validated(state) {
  return reduce(state, {
    type: "VALIDATED",
    report: {
      plan_ref: { plan_id: "PLAN-CHAT", revision: state.plan.revision },
      blocking: [],
      warnings: [],
    },
  });
}

function compiled(state) {
  return reduce(state, {
    type: "COMPILED",
    execution: {
      compiled: true,
      plan_ref: { plan_id: "PLAN-CHAT", revision: state.plan.revision, execution_plan_id: "EXE-1" },
      batches: [{ index: 0, tasks: ["C1"], bindings: {}, waiting: [] }],
      nodes: ["C1"],
      edges: [],
    },
  });
}

test("确认规格后只读，生成计划后进入草稿状态", () => {
  let state = reduce(initialState(), { type: "SPEC_LOADED", draft: clone(specDraft) });
  assert.equal(state.phase, PHASE.SPEC_DRAFT);

  state = reduce(state, { type: "SPEC_CONFIRMED", ref: { spec_id: "SPEC-CHAT", version: 1 } });
  assert.equal(state.phase, PHASE.SPEC_CONFIRMED);
  assert.equal(state.spec.locked, true);

  state = reduce(state, { type: "JOB_SUCCEEDED", plan: clone(planFixture) });
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
  assert.equal(state.plan.revision, 1);
  assert.equal(state.history.length, 1);
  assert.equal(state.selectedTaskId, "C1");
});

test("修改任务会提升修订、清空校验与编排，并保留旧版本", () => {
  let state = compiled(validated(confirmedPlan()));
  assert.equal(state.phase, PHASE.PLAN_COMPILED);

  state = reduce(state, { type: "TASK_PATCH", taskId: "C2", patch: { title: "改过的标题" } });
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
  assert.equal(state.plan.revision, 2);
  assert.equal(state.stale, true);
  assert.equal(state.validation, null);
  assert.equal(state.execution, null);
  const old = state.history.find((entry) => entry.revision === 1);
  assert.ok(old, "旧修订必须留在历史里");
  assert.equal(old.tasks.find((task) => task.id === "C2").title, "实现消息写入与鉴权");
  assert.equal(state.plan.tasks.find((task) => task.id === "C2").title, "改过的标题");
});

test("校验报告不是当前修订时拒绝编排", () => {
  let state = confirmedPlan();
  state = reduce(state, {
    type: "COMPILED",
    execution: {
      compiled: true,
      plan_ref: { plan_id: "PLAN-CHAT", revision: 1, execution_plan_id: "EXE-OLD" },
      batches: [],
    },
  });
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
  assert.match(state.message.text, /重新校验|不属于当前修订/);

  state = validated(state);
  assert.equal(state.phase, PHASE.PLAN_VALIDATED);
  state = reduce(state, { type: "TASK_PATCH", taskId: "C3", patch: { title: "新标题" } });
  assert.equal(state.plan.revision, 2);
  state = reduce(state, {
    type: "COMPILED",
    execution: {
      compiled: true,
      plan_ref: { plan_id: "PLAN-CHAT", revision: 1, execution_plan_id: "EXE-OLD" },
      batches: [],
    },
  });
  assert.equal(state.execution, null);
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
});

test("阻断问题未清除时不能进入已校验", () => {
  let state = confirmedPlan();
  state = reduce(state, {
    type: "VALIDATED",
    report: {
      plan_ref: { plan_id: "PLAN-CHAT", revision: 1 },
      blocking: [{ code: "MISSING_VERIFICATION", message: "缺验证任务" }],
      warnings: [],
    },
  });
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
  assert.match(state.message.text, /阻断/);
});

test("确认需要编排结果属于当前修订且未过期", () => {
  let state = compiled(validated(confirmedPlan()));
  const confirmed = reduce(state, { type: "CONFIRMED", revision: 1 });
  assert.equal(confirmed.phase, PHASE.PLAN_CONFIRMED);
  assert.equal(confirmed.history.at(-1).phase, PHASE.PLAN_CONFIRMED);

  const staleRevision = reduce(state, { type: "CONFIRMED", revision: 9 });
  assert.equal(staleRevision.phase, PHASE.PLAN_COMPILED);
  assert.match(staleRevision.message.text, /已过期/);

  state = reduce(state, { type: "TASK_PATCH", taskId: "C1", patch: { title: "x" } });
  state = validated(state);
  const withoutCompile = reduce(state, { type: "CONFIRMED", revision: state.plan.revision });
  assert.notEqual(withoutCompile.phase, PHASE.PLAN_CONFIRMED);
});

test("选择任务不改动修订与阶段", () => {
  const state = compiled(validated(confirmedPlan()));
  const next = reduce(state, { type: "SELECT_TASK", taskId: "C5" });
  assert.equal(next.plan.revision, state.plan.revision);
  assert.equal(next.phase, state.phase);
  assert.equal(next.selectedTaskId, "C5");
});

test("确认后编辑会派生新草稿，旧确认版仍在历史中", () => {
  let state = compiled(validated(confirmedPlan()));
  state = reduce(state, { type: "CONFIRMED", revision: 1 });
  state = reduce(state, { type: "TASK_PATCH", taskId: "C4", patch: { priority: 0 } });
  assert.equal(state.phase, PHASE.PLAN_DRAFT);
  assert.equal(state.plan.revision, 2);
  assert.match(state.message.text, /确认版保持只读/);
  assert.equal(state.history.find((entry) => entry.revision === 1).phase, PHASE.PLAN_CONFIRMED);
});

test("新增与删除任务都会产生新修订并清理编排结果", () => {
  let state = compiled(validated(confirmedPlan()));
  const before = state.plan.tasks.length;
  state = reduce(state, {
    type: "TASK_ADD",
    task: {
      id: "T-09",
      title: "新任务",
      kind: "implementation",
      role: "backend",
      depends_on: [],
      acceptance: ["可以验收"],
      priority: 1,
    },
  });
  assert.equal(state.plan.tasks.length, before + 1);
  assert.equal(state.plan.revision, 2);
  assert.equal(state.execution, null);

  state = reduce(state, { type: "TASK_DELETE", taskId: "C2" });
  assert.equal(state.plan.tasks.some((task) => task.id === "C2"), false);
  assert.equal(
    state.plan.tasks.some((task) => (task.depends_on ?? []).some((dep) => (dep.task_id ?? dep) === "C2")),
    false,
    "删除任务后不应残留指向它的依赖",
  );
});
