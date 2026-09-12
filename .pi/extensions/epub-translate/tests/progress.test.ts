import test from "node:test";
import assert from "node:assert/strict";
import { formatProgress } from "../progress.ts";

test("formats active progress from a SQLite summary", () => {
  assert.deepEqual(
    formatProgress(
      {
        state: "running",
        mergeState: "pending",
        total: 12,
        done: 5,
        leased: 3,
        failed: 1,
        pending: 3,
        startedAt: 0,
        cancelRequested: false,
      },
      65_000,
    ),
    ["EPUB translating · 5/12 complete · 3 active · 1 failed · 1m 5s"],
  );
});

test("formats terminal and cancellation states", () => {
  const base = {
    mergeState: "pending",
    total: 2,
    done: 1,
    leased: 0,
    failed: 1,
    pending: 0,
    startedAt: 0,
    cancelRequested: false,
  };
  assert.deepEqual(formatProgress({ ...base, state: "failed" }, 0), [
    "EPUB needs retry · 1/2 · 1 failed",
  ]);
  assert.deepEqual(formatProgress({ ...base, state: "running", cancelRequested: true }, 0), [
    "EPUB cancellation requested · 1/2",
  ]);
  assert.deepEqual(formatProgress({ ...base, state: "running", mergeState: "leased", done: 2, failed: 0 }, 0), [
    "EPUB merging · 2/2",
  ]);
  assert.deepEqual(formatProgress({ ...base, state: "completed", done: 2, failed: 0 }, 0), [
    "EPUB complete · 2/2",
  ]);
});
