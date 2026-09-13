import assert from "node:assert/strict";
import { test } from "node:test";
import { helperPath } from "../src/worker-tools.ts";

test("resolves the bundled run store relative to the extension", () => {
  assert.match(helperPath(), /[\\/]src[\\/]run_store\.py$/);
});
