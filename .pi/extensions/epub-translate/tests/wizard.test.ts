import test from "node:test";
import assert from "node:assert/strict";
import {
  modelIdentifier,
  parseJsonResponse,
  selectableModels,
  selectableThinkingLevels,
} from "../wizard.ts";

test("selectableModels prefers Pi session-scoped models", () => {
  const scoped = [{ model: { provider: "scoped", id: "one", name: "One" }, thinkingLevel: "high" }];
  const available = [{ provider: "catalog", id: "two", name: "Two" }];

  assert.deepEqual(selectableModels(scoped, available), [scoped[0].model]);
  assert.equal(modelIdentifier(scoped[0].model), "scoped/one");
});

test("thinking levels are limited for non-reasoning models", () => {
  assert.deepEqual(selectableThinkingLevels({ reasoning: false }), ["off"]);
  assert.deepEqual(selectableThinkingLevels({ reasoning: true }), ["off", "minimal", "low", "medium", "high", "xhigh", "max"]);
});

test("parseJsonResponse accepts fenced model JSON", () => {
  assert.deepEqual(parseJsonResponse("Here is the result:\n```json\n{\"ok\":true}\n```"), { ok: true });
});
