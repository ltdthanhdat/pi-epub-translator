import test from "node:test";
import assert from "node:assert/strict";
import {
  inferLanguageCode,
  modelIdentifier,
  normalizeScopeAnalysis,
  parseJsonResponse,
  selectableModels,
  selectableThinkingLevels,
} from "../src/wizard.ts";

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

test("infers common BCP-47 codes from the target language", () => {
  assert.equal(inferLanguageCode("Vietnamese"), "vi");
  assert.equal(inferLanguageCode("tiếng Việt"), "vi");
  assert.equal(inferLanguageCode("English (United States)"), "en-US");
});

test("normalizes AI scope and assigns every document exactly once", () => {
  const documents = [
    { path: "chapter.xhtml", in_spine: true, is_nav: false },
    { path: "notes.xhtml", in_spine: false, is_nav: false },
    { path: "nav.xhtml", in_spine: false, is_nav: true },
  ];

  assert.deepEqual(
    normalizeScopeAnalysis(
      { translate: ["chapter.xhtml", "nav.xhtml"], keep: [], target_language_code: "vi" },
      documents,
    ),
    {
      translate: ["chapter.xhtml", "nav.xhtml"],
      keep: ["notes.xhtml"],
      targetLanguageCode: "vi",
    },
  );
});
