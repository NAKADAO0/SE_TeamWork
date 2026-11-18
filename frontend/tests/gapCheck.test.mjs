import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { gapCheck, parseSpecMarkdownClient } from "../src/features/spec-editor/gapCheck.js";

const specDraft = JSON.parse(
  await readFile(new URL("../fixtures/spec-chat.json", import.meta.url), "utf8"),
);
const clone = (value) => JSON.parse(JSON.stringify(value));
const codes = (list) => list.map((item) => item.code);

test("聊天样例规格没有缺口", () => {
  const result = gapCheck(clone(specDraft));
  assert.deepEqual(codes(result.errors), []);
});

test("空规格会同时报出产品、范围与需求缺口", () => {
  const result = gapCheck({});
  const found = codes(result.errors);
  assert.ok(found.includes("MISSING_PRODUCT"));
  assert.ok(found.includes("MISSING_SCOPE"));
  assert.ok(found.includes("MISSING_REQUIREMENTS"));
  assert.ok(result.errors.every((item) => item.field_path));
});

test("需求编号重复与验收缺失都能定位到字段", () => {
  const draft = clone(specDraft);
  draft.requirements[1].id = draft.requirements[0].id;
  draft.requirements[0].acceptance = [""];
  const found = codes(gapCheck(draft).errors);
  assert.ok(found.includes("DUPLICATE_REQUIREMENT_ID"));
  assert.ok(found.includes("MISSING_REQUIREMENT_ACCEPTANCE"));
});

test("模块引用不存在的需求会报错", () => {
  const draft = clone(specDraft);
  draft.modules[0].requirement_ids.push("R-NOT-EXIST");
  const result = gapCheck(draft);
  const issue = result.errors.find((item) => item.code === "UNKNOWN_MODULE_REQUIREMENT");
  assert.ok(issue);
  assert.match(issue.message, /R-NOT-EXIST/);
});

test("一句话产品意图只给出缺口，不冒充完整规格", () => {
  const result = gapCheck({ product: { name: "我要做微信" }, requirements: [] });
  assert.ok(result.errors.some((item) => item.code === "MISSING_SCOPE"));
  assert.ok(result.errors.some((item) => item.code === "MISSING_REQUIREMENTS"));
});

test("契约 DesignSpec 与现场 Markdown 规格也能通过缺口检查", async () => {
  const contractSpec = JSON.parse(
    await readFile(new URL("../../examples/contracts/chat/design-spec.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(codes(gapCheck(contractSpec).errors), []);

  const unseenMd = await readFile(new URL("../../examples/unseen-spec/spec.md", import.meta.url), "utf8");
  const parsedMd = parseSpecMarkdownClient(unseenMd);
  assert.equal(parsedMd.requirements.length, 4);
  assert.equal(parsedMd.modules.length, 4);
  assert.deepEqual(codes(gapCheck(unseenMd).errors), []);
});
