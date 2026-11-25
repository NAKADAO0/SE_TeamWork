import test from "node:test";
import assert from "node:assert/strict";
import { graphFeedback, executionGraph } from "../src/studio/executionGraph.js";
const flow = {
  nodes: ["collaborate", "generate", "review", "confirm"].map((id) => ({
    id,
    label: id,
  })),
  edges: [
    { source: "collaborate", target: "generate" },
    { source: "generate", target: "review" },
    { source: "review", target: "confirm" },
  ],
};
const feedback = (extra = {}) => ({
  scope: "internal",
  stage_id: "requirements",
  epoch: 2,
  source_node: "confirm",
  target_node: "generate",
  resolved: false,
  reason: "修改范围",
  ...extra,
});
const run = (items, extra = {}) => ({
  stage: "requirements",
  status: "running",
  stages: { requirements: { epoch: 2 } },
  feedback: items,
  ...extra,
});
test("main flow follows definition order on a row or column", () => {
  for (const width of [900, 390]) {
    const html = executionGraph(flow, [], width, () => "waiting", null);
    const points = [...html.matchAll(/--node-x:(\d+)px;--node-y:(\d+)px/g)].map(
      (m) => [Number(m[1]), Number(m[2])],
    );
    assert.equal(points.length, 4);
    assert.equal(new Set(points.map((p) => p[width < 680 ? 0 : 1])).size, 1);
    assert.ok(
      points.every(
        (p, i) =>
          !i || p[width < 680 ? 1 : 0] > points[i - 1][width < 680 ? 1 : 0],
      ),
    );
    assert.deepEqual(
      [...html.matchAll(/data-node="([^"]+)"/g)].map((m) => m[1]),
      flow.nodes.map((n) => n.id),
    );
  }
});
test("only recorded, unresolved, current-epoch returns show automatically", () => {
  const items = [
    feedback(),
    feedback({ resolved: true }),
    feedback({ epoch: 1 }),
    feedback({ source_node: null }),
    feedback({ stage_id: "product" }),
  ];
  assert.equal(graphFeedback(run(items), "requirements").length, 1);
  assert.equal(graphFeedback(run(items), "requirements")[0].count, 1);
  assert.equal(
    graphFeedback(
      run(items, { status: "awaiting_confirmation" }),
      "requirements",
    ).length,
    0,
  );
});
test("history aggregates actual endpoints and never infers legacy endpoints", () => {
  const items = [
    feedback(),
    feedback({ resolved: true }),
    {
      scope: "internal",
      source_stage: "requirements",
      target_stage: "requirements",
    },
  ];
  const result = graphFeedback(run(items), "requirements", true);
  assert.equal(result.length, 1);
  assert.equal(result[0].count, 2);
  assert.equal(result[0].source, "confirm");
  assert.equal(result[0].target, "generate");
});
test("feedback is escaped and hidden when no actual return exists", () => {
  const html = executionGraph(
    flow,
    [
      {
        source: "review",
        target: "generate",
        active: true,
        count: 1,
        reason: "<script>alert(1)</script>",
      },
    ],
    900,
    () => "waiting",
    null,
  );
  assert.match(html, /execution-return active/);
  assert.ok(!html.includes("<script>"));
  assert.match(html, /&lt;script&gt;/);
  assert.ok(
    !executionGraph(flow, [], 900, () => "waiting", null).includes(
      'class="execution-return',
    ),
  );
});
