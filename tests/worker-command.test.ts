import test from "node:test";
import assert from "node:assert/strict";
import { buildWorkerCommand, WORKER_PROMPT, WORKER_TOOL_NAMES } from "../src/worker-command.ts";

test("builds a fresh no-session Pi worker command with only EPUB tools", () => {
  const worker = buildWorkerCommand("/repo/.parallel-translate/book", "openai/gpt-5", "high");

  assert.equal(worker.command, "pi");
  assert.deepEqual(worker.args.slice(0, -1), [
    "--approve",
    "--no-session",
    "--no-context-files",
    "--no-skills",
    "--no-prompt-templates",
    "--no-builtin-tools",
    "--tools",
    WORKER_TOOL_NAMES.join(","),
    "--model",
    "openai/gpt-5",
    "--thinking",
    "high",
    "-p",
  ]);
  assert.equal(worker.args.at(-1), WORKER_PROMPT);
});
