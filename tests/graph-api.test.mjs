import assert from "node:assert/strict";
import test from "node:test";
import { loadRemoteGraph } from "../demo/graph-api.js";

test("api=http keeps the snapshot on the host serving the graph", async (t) => {
  const original = globalThis.fetch;
  globalThis.location = { port: "8054" };
  t.after(() => { globalThis.fetch = original; delete globalThis.location; });
  const requests = [];
  globalThis.fetch = async (url) => {
    requests.push(url);
    return { ok: true, json: async () => url === "/plans" ? [{ plan_id: "PLAN-TEST" }]
      : url.endsWith("/revisions") ? [{ revision: 1 }] : { plan: { revision: 1 } } };
  };
  await loadRemoteGraph(new URLSearchParams("api=http&plan_id=PLAN-TEST&revision=1"));
  assert.deepEqual(requests, ["/plans", "/plans/PLAN-TEST/revisions", "/plans/PLAN-TEST/graph?revision=1"]);
});
