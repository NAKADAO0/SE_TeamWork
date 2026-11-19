/**
 * 视图冒烟测试：用最小 DOM 替身跑一遍 mount + update，确保模板渲染不抛错。
 * 不验证像素与真实事件，只保证每个视图能在真实状态下走完一次渲染。
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createStore } from "../src/shared/store.js";
import { initialState, reduce } from "../src/shared/reducer.js";
import { mountSpecEditor } from "../src/features/spec-editor/specEditor.js";
import { mountTaskTable } from "../src/features/task-editor/taskTable.js";
import { mountTaskDetail } from "../src/features/task-editor/taskDetail.js";
import { mountBatchPanel } from "../src/features/task-editor/batchPanel.js";
import { mountGraph } from "../src/features/workflow-graph/graphCanvas.js";

class StubElement {
  constructor(selector = "") {
    this.selector = selector;
    this.innerHTML = "";
    this.textContent = "";
    this.dataset = {};
    this.style = {};
    this.value = "";
    this.disabled = false;
    this.hidden = false;
    this.readOnly = false;
    this.classList = { add() {}, toggle() {}, remove() {}, contains: () => false };
    this.listeners = {};
    this.queries = new Map();
  }
  querySelector(selector) {
    if (!this.queries.has(selector)) this.queries.set(selector, new StubElement(selector));
    return this.queries.get(selector);
  }
  querySelectorAll() {
    return [];
  }
  addEventListener(type, handler) {
    (this.listeners[type] ??= []).push(handler);
  }
  setAttribute() {}
  append() {}
  replaceChildren() {}
  closest() {
    return null;
  }
}

globalThis.document = {
  activeElement: null,
  body: new StubElement("body"),
  querySelector: (selector) => new StubElement(selector),
  createElement: (tag) => new StubElement(tag),
  createElementNS: () => new StubElement("svg"),
};

const specDraft = JSON.parse(
  await readFile(new URL("../fixtures/spec-chat.json", import.meta.url), "utf8"),
);
const planFixture = JSON.parse(
  await readFile(new URL("../fixtures/taskplan-chat.json", import.meta.url), "utf8"),
);
const clone = (value) => JSON.parse(JSON.stringify(value));

function hydratedState() {
  let state = reduce(initialState(), { type: "SPEC_LOADED", draft: clone(specDraft) });
  state = reduce(state, { type: "SPEC_CONFIRMED", ref: { spec_id: "SPEC-CHAT", version: 1 } });
  state = reduce(state, { type: "JOB_SUCCEEDED", plan: clone(planFixture) });
  state = reduce(state, {
    type: "VALIDATED",
    report: {
      plan_ref: { plan_id: "PLAN-CHAT", revision: 1 },
      blocking: [],
      warnings: [
        { severity: "warning", code: "MANY_ACCEPTANCE_LINES", object_id: "C2", message: "验收条件较多", field_path: "acceptance" },
      ],
    },
  });
  return reduce(state, {
    type: "COMPILED",
    execution: {
      compiled: true,
      plan_ref: { plan_id: "PLAN-CHAT", revision: 1, execution_plan_id: "EXE-1" },
      batches: [
        { index: 0, tasks: ["C1"], bindings: {}, waiting: [] },
        { index: 1, tasks: ["C2", "C3"], bindings: {}, waiting: [{ task_id: "C4", reason: "backend 容量已满" }] },
      ],
      nodes: ["C1", "C2", "C3", "C4"],
      edges: [{ from: "C1", to: "C2" }],
    },
  });
}

const noopActions = new Proxy(
  {},
  {
    get: () => () => {},
  },
);

test("规格编辑器可以渲染草稿与已确认两种状态", () => {
  const store = createStore(initialState(), reduce);
  const root = new StubElement();
  const view = mountSpecEditor(root, { store, actions: noopActions });
  view.setTemplates([{ template_id: "default", name: "标准", max_concurrency: 2 }]);

  const state = hydratedState();
  assert.doesNotThrow(() => view.update(reduce(state, { type: "SPEC_TEXT_CHANGE", text: "{}" })));
  assert.doesNotThrow(() => view.update(state));
  assert.match(root.innerHTML, /产品设计规格/);
});

test("任务表渲染全部任务与问题标记", () => {
  const store = createStore(hydratedState(), reduce);
  const root = new StubElement();
  const view = mountTaskTable(root, { store, actions: noopActions });
  view.update(store.getState());
  const body = root.querySelector('[data-role="body"]');
  assert.match(body.innerHTML, /C1/);
  assert.match(body.innerHTML, /C8/);
  assert.equal(root.querySelector('[data-role="count"]').textContent, "8 项");
});

test("任务详情渲染依赖、产物与追溯字段", () => {
  const store = createStore(hydratedState(), reduce);
  const root = new StubElement();
  const view = mountTaskDetail(root, { store, actions: noopActions });
  view.update(store.getState());
  assert.match(root.innerHTML, /任务详情/);
  assert.match(root.innerHTML, /C1/);
});

test("批次面板同时渲染问题与等待原因", () => {
  const store = createStore(hydratedState(), reduce);
  const root = new StubElement();
  const view = mountBatchPanel(root, { store });
  view.update(store.getState());
  assert.match(root.querySelector('[data-role="batches"]').innerHTML, /批次 1/);
  assert.match(root.querySelector('[data-role="batches"]').innerHTML, /backend 容量已满/);
  assert.match(root.querySelector('[data-role="report"]').innerHTML, /警告 1/);
});

test("依赖图渲染节点与连线", () => {
  const store = createStore(hydratedState(), reduce);
  const root = new StubElement();
  const view = mountGraph(root, { store, actions: noopActions });
  view.update(store.getState());
  const board = root.querySelector('[data-role="board"]');
  const svg = root.querySelector('[data-role="svg"]');
  assert.match(board.innerHTML, /ws-node/);
  assert.match(board.innerHTML, /C1/);
  assert.match(svg.innerHTML, /ws-edge/);
});

test("范围编辑阶段只确认范围，生成后恢复规格确认并支持返回修改", () => {
  const store = createStore(initialState(), reduce);
  const root = new StubElement();
  const view = mountSpecEditor(root, { store, actions: noopActions });
  const button = (name) => root.querySelector(`[data-action="${name}"]`);
  store.dispatch({
    type: "SPEC_LOADED", text: "# 产品\n## 产品范围\n- 提交预约", draft: {},
    extraction: { stage: "scope", source: "原始说明" },
  });
  store.dispatch({ type: "SPEC_TEXT_CHANGE", text: "# 产品\n## 产品范围\n- 仅学生可提交预约" });
  assert.equal(store.getState().extraction.source, "原始说明");
  view.update(store.getState());
  assert.equal(button("confirm-scope").textContent, "确认范围并生成规格");
  assert.equal(button("confirm").disabled, true);
  assert.equal(button("plan").hidden, false);
  assert.equal(root.querySelector('[data-role="text"]').readOnly, false);
  store.dispatch({
    type: "SPEC_LOADED", draft: clone(specDraft),
    extraction: { stage: "full", source: "原始说明", confirmedScope: store.getState().specText },
  });
  view.update(store.getState());
  assert.equal(button("confirm").textContent, "确认规格");
  assert.equal(button("plan").hidden, false);
  assert.equal(button("plan").disabled, true);
  assert.equal(button("reopen-scope").hidden, false);
  store.dispatch({ type: "SPEC_LOADED", draft: clone(specDraft) });
  assert.equal(store.getState().extraction, null);
});
