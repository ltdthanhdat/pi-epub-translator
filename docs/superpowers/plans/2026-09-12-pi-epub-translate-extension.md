# Pi EPUB Translation Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-local Pi TUI EPUB translator whose fresh `pi -p` workers atomically claim, translate, retry, and finally merge EPUB fragments without a coordinator loop.

**Architecture:** `.pi/extensions/epub-translate/` provides the Pi commands, TUI and worker custom tools. `run_store.py` is a dependency-free Python helper owning the SQLite schema and deterministic EPUB/XML mechanics; its JSON CLI is the only durable-state boundary. The extension starts a finite seed of Pi processes; each completed process spawns exactly one fresh replacement, and a SQLite merge lease elects the sole merger.

**Tech Stack:** Pi 0.85.1 extension API, TypeScript loaded by Pi/jiti, `@earendil-works/pi-tui`, TypeBox, Python 3.13 standard library (`sqlite3`, `xml.etree.ElementTree`, `zipfile`, `subprocess`), `unittest`, `tsx` for pure TypeScript tests.

**Spec:** `docs/superpowers/specs/2026-09-12-pi-epub-translate-design.md`

## Global Constraints

- Keep `.agents/skills/translating-epub-books/` unchanged for Codex users.
- Install the extension only at `.pi/extensions/epub-translate/`; no web or external application UI.
- `input/` contains source books; only publish completed books under `output/` and never overwrite a source EPUB.
- All run state is in a run-local SQLite database; the TUI is read-only with respect to scheduling.
- A translation worker is a fresh `pi -p --no-session` process for one fragment; no worker conversation is reused.
- User reviews per-document translation scope and locks the glossary before jobs are created.
- Preserve every XHTML tag and attribute, update only `lang`/`xml:lang` on selected XHTML documents, and preserve required EPUB ZIP layout.
- Do not publish an EPUB unless all selected jobs have completed and verification passes.

---

## File structure

```text
.pi/extensions/epub-translate/
├── package.json                    # dev-only tsx test runner
├── run_store.py                    # SQLite + EPUB/XML + JSON CLI
├── worker-tools.ts                 # claim/complete/spawn/merge custom tools
├── progress.ts                     # pure run summary + widget lines
├── index.ts                        # commands, wizard, analysis, worker seed
└── tests/
    ├── test_run_store.py           # helper unit/integration tests
    └── progress.test.ts            # pure TypeScript summary/widget tests
```

The existing `.agents/skills/translating-epub-books/scripts/parallel_translate.py` and its test file remain untouched.

## Task 1: Create the tested run-store contract and SQLite lifecycle

**Files:**
- Create: `.pi/extensions/epub-translate/run_store.py`
- Create: `.pi/extensions/epub-translate/tests/test_run_store.py`

**Interfaces:**
- Produces `RunStore(db_path: Path)` with `create_run(config)`, `claim(worker_id)`, `heartbeat(job_id, worker_id, lease_token)`, `complete(job_id, worker_id, lease_token, result_path)`, `fail(job_id, worker_id, lease_token, error)`, `request_cancel()`, `retry_failed(job_ids | None)`, `summary()`, and `try_claim_merge(worker_id)`.
- Job records contain `id`, `xhtml_path`, `locator`, `input_path`, `state`, `worker_id`, `lease_token`, `lease_until`, `attempts`, `result_path`, and `error`.
- Run records contain model identifier, thinking level, target language/code, locked glossary path, `state`, `cancel_requested`, and `merge_state`.

- [ ] **Step 1: Write failing concurrency and fencing tests**

```python
def test_claim_returns_one_distinct_job_per_worker(self):
    store = RunStore(self.tmp / "run.sqlite")
    store.add_jobs([draft("0"), draft("1")])
    first = store.claim("worker-a", now=100)
    second = store.claim("worker-b", now=100)
    self.assertEqual({first.id, second.id}, {1, 2})


def test_stale_lease_token_cannot_complete_after_reclaim(self):
    store = seeded_store(self.tmp)
    old = store.claim("worker-a", now=100)
    new = store.claim("worker-b", now=100 + LEASE_SECONDS + 1)
    self.assertFalse(store.complete(old.id, "worker-a", old.lease_token, "old.xhtml"))
    self.assertTrue(store.complete(new.id, "worker-b", new.lease_token, "new.xhtml"))
```

- [ ] **Step 2: Run the new tests and confirm they fail because `run_store` does not exist**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'run_store'`.

- [ ] **Step 3: Implement the minimal SQLite schema and conditional updates**

```python
LEASE_SECONDS = 120

@dataclass(frozen=True)
class ClaimedJob:
    id: int
    xhtml_path: str
    locator: str
    input_path: str
    lease_token: str
    attempts: int


def claim(self, worker_id: str, now: int | None = None) -> ClaimedJob | None:
    now = int(time.time()) if now is None else now
    token = uuid.uuid4().hex
    with self.connection() as db:
        row = db.execute("""
          UPDATE jobs SET state='leased', worker_id=?, lease_token=?,
             lease_until=?, attempts=attempts+1
          WHERE id=(SELECT id FROM jobs
             WHERE state='pending' OR (state='leased' AND lease_until < ?)
             ORDER BY id LIMIT 1)
          AND (SELECT cancel_requested FROM runs WHERE id=1)=0
          RETURNING *
        """, (worker_id, token, now + LEASE_SECONDS, now)).fetchone()
    return ClaimedJob.from_row(row) if row else None
```

Implement `heartbeat`, `complete`, and `fail` with `WHERE id=? AND state='leased' AND worker_id=? AND lease_token=?`. `complete` writes a unique immutable `results/<job-id>-<lease-token>.xhtml` through a temporary file plus `Path.replace()`, then conditionally stores that exact path in the job row; it never writes a shared `results/<job-id>.xhtml` path. Add `runs`/`jobs` indexes for state and lease expiry.

- [ ] **Step 4: Run the lifecycle test file**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v`

Expected: PASS for distinct claims, heartbeat ownership, stale-token fencing, cancellation blocking claims, retry reset, and one merge-lease winner.

- [ ] **Step 5: Commit the standalone store contract**

```bash
git add .pi/extensions/epub-translate/run_store.py .pi/extensions/epub-translate/tests/test_run_store.py
git commit -m "feat: add EPUB translation run store"
```

## Task 2: Add EPUB preparation, document-scope data, glossary candidates, and merge verification

**Files:**
- Modify: `.pi/extensions/epub-translate/run_store.py`
- Modify: `.pi/extensions/epub-translate/tests/test_run_store.py`

**Interfaces:**
- Produces `inspect_documents(workspace) -> list[DocumentInfo]`, `prepare_run(source_epub, run_dir, config, selected_paths)`, `sample_documents(workspace, selected_paths, sample_count=6)`, `glossary_candidates(samples)`, `merge_run(run_dir)`, and `verify_output(workspace, output_epub, selected_paths)`.
- `DocumentInfo` includes manifest-relative XHTML path, spine/nav membership, `epub:type` values, document title, and bounded text sample.
- `glossary_candidates` returns ranked `{term, frequency, document_count, contexts}` records; it does not call a model.

- [ ] **Step 1: Write failing mechanical tests**

```python
def test_inspect_documents_reports_each_xhtml_without_filename_rules(self):
    infos = inspect_documents(self.workspace)
    self.assertEqual({"text/chap.xhtml", "text/notes-weird.xhtml"}, {i.path for i in infos})
    self.assertIn("footnote", infos_by_path["text/notes-weird.xhtml"].epub_types)


def test_glossary_candidates_remove_urls_page_numbers_and_singletons(self):
    candidates = glossary_candidates([
        "Nguyen Van A met Nguyen Van A. https://example.test 42",
        "Nguyen Van A returned to Hanoi.",
    ])
    self.assertEqual("Nguyen Van A", candidates[0].term)
    self.assertNotIn("https", " ".join(c.term for c in candidates))


def test_merge_refuses_missing_results_and_preserves_epub_layout(self):
    with self.assertRaisesRegex(ValueError, "unfinished"):
        merge_run(self.run_dir)
```

- [ ] **Step 2: Run the mechanical tests and confirm they fail**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v`

Expected: FAIL because inspection, candidate extraction, and merge functions are undefined.

- [ ] **Step 3: Implement EPUB mechanics by adapting—not importing—the existing skill logic**

```python
TEXT_BLOCKS = {"p", "li", "td", "th", "caption", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}


def glossary_candidates(samples: list[str]) -> list[Candidate]:
    occurrences: dict[str, list[Context]] = {}
    for document_index, text in enumerate(samples):
        for phrase in candidate_phrases(text):
            if is_url(phrase) or is_page_number(phrase) or is_stopword_only(phrase):
                continue
            occurrences.setdefault(normalize_phrase(phrase), []).append(Context(document_index, text))
    return rank_candidates(occurrences)
```

Copy only the tested XML fragment, pagebreak protection/restoration, OPF parsing, staging build, and ZIP verification concepts needed from the retained skill. Do not import its files, and do not add filename-based Index/Notes exclusions. `merge_run` must claim nothing; it receives a run that already owns `merge_state` and verifies all selected jobs are `done`.

- [ ] **Step 4: Expose a newline-delimited JSON helper CLI**

```bash
python3 .pi/extensions/epub-translate/run_store.py inspect --run .parallel-translate/book
python3 .pi/extensions/epub-translate/run_store.py summary --run .parallel-translate/book
python3 .pi/extensions/epub-translate/run_store.py claim --run .parallel-translate/book --worker-id abc
```

Each successful command writes exactly one JSON object to stdout; expected user/action errors write JSON `{ "error": "..." }` and exit nonzero. This is the TypeScript extension boundary.

- [ ] **Step 5: Run all helper tests**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v`

Expected: PASS, including scope inspection, candidate filtering, structure validation, pagebreak restoration, cancelled/unfinished merge refusal, ZIP member/layout checks, and optional `epubcheck` invocation mocked in tests.

- [ ] **Step 6: Commit EPUB mechanics**

```bash
git add .pi/extensions/epub-translate/run_store.py .pi/extensions/epub-translate/tests/test_run_store.py
git commit -m "feat: prepare and verify EPUB translation runs"
```

## Task 3: Implement pure progress formatting and test support

**Files:**
- Create: `.pi/extensions/epub-translate/package.json`
- Create: `.pi/extensions/epub-translate/progress.ts`
- Create: `.pi/extensions/epub-translate/tests/progress.test.ts`

**Interfaces:**
- `RunSummary` mirrors helper JSON: `{ state, mergeState, total, done, leased, failed, pending, startedAt, cancelRequested }`.
- `formatProgress(summary, nowMs) -> string[]` returns unstyled lines suitable for `ctx.ui.setWidget`.
- `package.json` has `scripts.test = "tsx --test tests/*.test.ts"` and dev dependency `tsx`; runtime code adds no npm dependency.

- [ ] **Step 1: Write failing widget snapshot tests**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { formatProgress } from "../progress.ts";

test("formats active progress from SQLite summary", () => {
  assert.deepEqual(formatProgress({ state: "running", mergeState: "pending", total: 12,
    done: 5, leased: 3, failed: 1, pending: 3, startedAt: 0, cancelRequested: false }, 65_000), [
    "EPUB translating · 5/12 complete · 3 active · 1 failed · 1m 5s",
  ]);
});
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `cd .pi/extensions/epub-translate && npm install && npm test`

Expected: FAIL with module-not-found for `../progress.ts`.

- [ ] **Step 3: Implement state-to-widget formatting without accessing Pi APIs**

```ts
export function formatProgress(summary: RunSummary, nowMs: number): string[] {
  if (summary.state === "completed") return [`EPUB complete · ${summary.done}/${summary.total}`];
  if (summary.state === "failed") return [`EPUB needs retry · ${summary.done}/${summary.total} · ${summary.failed} failed`];
  if (summary.cancelRequested) return [`EPUB cancellation requested · ${summary.done}/${summary.total}`];
  if (summary.mergeState === "leased") return [`EPUB merging · ${summary.done}/${summary.total}`];
  return [`EPUB translating · ${summary.done}/${summary.total} complete · ${summary.leased} active · ${summary.failed} failed · ${formatElapsed(summary.startedAt, nowMs)}`];
}
```

- [ ] **Step 4: Run TypeScript tests**

Run: `cd .pi/extensions/epub-translate && npm test`

Expected: PASS for active, merging, completed, failed, and cancelled summaries.

- [ ] **Step 5: Commit UI-pure code and test runner files**

```bash
git add .pi/extensions/epub-translate/package.json .pi/extensions/epub-translate/package-lock.json .pi/extensions/epub-translate/progress.ts .pi/extensions/epub-translate/tests/progress.test.ts
git commit -m "feat: format EPUB translation progress"
```

## Task 4: Build the extension helper bridge and worker command construction

**Files:**
- Create: `.pi/extensions/epub-translate/worker-tools.ts`
- Modify: `.pi/extensions/epub-translate/tests/progress.test.ts`

**Interfaces:**
- `runHelper(ctx, args, signal) -> Promise<HelperResponse>` executes `python3 run_store.py ...`, parses one JSON object, and throws on malformed/nonzero responses.
- `buildWorkerCommand(runDir, model, thinking) -> { command: "pi"; args: string[] }` returns a fresh no-session Pi invocation with `--approve`, `--no-builtin-tools`, `--tools epub_claim_job,epub_complete_job,epub_spawn_worker,epub_merge_run`, `--model`, `--thinking`, `-p`, and the fixed worker prompt.
- `registerWorkerTools(pi)` registers the four tool names and returns no state except process-local heartbeat handles.

- [ ] **Step 1: Write failing command construction tests**

```ts
test("worker command starts a fresh Pi process with only worker tools", () => {
  const worker = buildWorkerCommand("/repo/.parallel-translate/book", "openai/gpt-5", "high");
  assert.equal(worker.command, "pi");
  assert.deepEqual(worker.args.slice(0, 8), ["--approve", "--no-session", "--no-builtin-tools",
    "--tools", "epub_claim_job,epub_complete_job,epub_spawn_worker,epub_merge_run",
    "--model", "openai/gpt-5", "--thinking"]);
});
```

- [ ] **Step 2: Run TypeScript tests and confirm failure**

Run: `cd .pi/extensions/epub-translate && npm test`

Expected: FAIL because `buildWorkerCommand` is not exported.

- [ ] **Step 3: Implement the bridge, heartbeat, and tool schemas**

```ts
const workerPrompt = `Call epub_claim_job first. If no job is returned, exit. Translate only the returned XHTML fragment. Preserve every tag, attribute, and KEEP_PAGEBREAK marker. Call epub_complete_job with only translated XHTML. Then call epub_spawn_worker; if it reports merge ownership, call epub_merge_run. Exit.`;

pi.registerTool({
  name: "epub_claim_job",
  parameters: Type.Object({}),
  async execute(_id, _params, signal, _update, ctx) {
    const claim = await runHelper(ctx, ["claim", "--run", runDirFromEnv(), "--worker-id", workerId()], signal);
    startHeartbeat(claim, ctx);
    return { content: [{ type: "text", text: JSON.stringify(claim) }], details: claim };
  },
});
```

Use `EPUB_TRANSLATE_RUN`, `EPUB_TRANSLATE_WORKER_ID`, and `EPUB_TRANSLATE_MODEL` environment variables. `startHeartbeat` starts only after a successful claim, clears itself on complete/fail/session shutdown, and never starts in the extension factory. The complete tool must stop its heartbeat even when helper validation rejects the output.

- [ ] **Step 4: Run TypeScript tests and type-load smoke test**

Run: `cd .pi/extensions/epub-translate && npm test && cd ../../.. && pi --approve -e .pi/extensions/epub-translate/worker-tools.ts -p "Reply only: loaded"`

Expected: tests PASS; Pi exits successfully without an extension load error.

- [ ] **Step 5: Commit worker primitives**

```bash
git add .pi/extensions/epub-translate/worker-tools.ts .pi/extensions/epub-translate/tests/progress.test.ts
git commit -m "feat: add stateless Pi EPUB worker tools"
```

## Task 5: Implement scope/glossary analysis and the interactive translation wizard

**Files:**
- Create: `.pi/extensions/epub-translate/index.ts`
- Modify: `.pi/extensions/epub-translate/worker-tools.ts`
- Modify: `.pi/extensions/epub-translate/tests/progress.test.ts`

**Interfaces:**
- `/translate-epub` is interactive-only and starts `runTranslationWizard(ctx)`.
- `chooseModel(ctx)` uses `ctx.scopedModels` when nonempty, otherwise `ctx.modelRegistry.getAvailable()`, returning a Pi model identifier and supported thinking level.
- `analyzeJson(model, thinking, prompt, signal)` starts a fresh `pi -p --no-session --model ... --thinking ...` analysis process, parses only JSON, and does not use the current conversation.
- `DocumentSelection` is `{ path: string; include: boolean }[]`; `RunConfig` persists its locked scope and glossary.

- [ ] **Step 1: Write a failing pure wizard-input test for selected-model fallback**

```ts
test("selectableModels prefers the session-scoped catalog", () => {
  assert.deepEqual(selectableModels([{ model: { provider: "a", id: "one" } }], [{ provider: "b", id: "two" }]),
    [{ provider: "a", id: "one" }]);
});
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `cd .pi/extensions/epub-translate && npm test`

Expected: FAIL because `selectableModels` is not exported by `index.ts`.

- [ ] **Step 3: Implement the Pi-only wizard**

```ts
pi.registerCommand("translate-epub", {
  description: "Create and start an EPUB translation run",
  handler: async (_args, ctx) => {
    if (ctx.mode !== "tui") {
      ctx.ui.notify("/translate-epub requires Pi TUI", "error");
      return;
    }
    await runTranslationWizard(ctx);
  },
});
```

Use `ctx.ui.select` for the EPUB, model, thinking level, and worker count; use `ctx.ui.input` for target language and BCP-47 code; use `ctx.ui.editor` for the locked glossary. Render document scope with a `SettingsList` or `SelectList`-based custom component and require an explicit **Lock & start** confirmation after the glossary editor returns. Run inspection before scope review and invoke analysis only after the user has selected scope. Bound sampled text and candidate contexts before `analyzeJson`.

The analysis prompt must request JSON only with a documented schema. Reject malformed JSON, unknown document paths, no selected documents, empty language/code, or empty locked glossary without creating jobs.

- [ ] **Step 4: Seed workers only after the run is durable**

```ts
await runHelper(ctx, ["prepare", "--source", source, "--run", runDir, "--config-json", JSON.stringify(config)], undefined);
for (let index = 0; index < config.workers; index += 1) {
  await spawnDetachedWorker(runDir, config.model, config.thinking, `seed-${index}`);
}
```

Ensure the helper snapshots glossary text under the run directory before job insertion. A spawn failure must mark the run failed with the error; it must not delete successful workers or the resumable run.

- [ ] **Step 5: Run tests and manual wizard smoke check**

Run: `cd .pi/extensions/epub-translate && npm test && cd ../../.. && pi --approve -e .pi/extensions/epub-translate/index.ts`

Expected: tests PASS; `/translate-epub` appears in Pi command completion and can be opened then cancelled without creating a run.

- [ ] **Step 6: Commit wizard implementation**

```bash
git add .pi/extensions/epub-translate/index.ts .pi/extensions/epub-translate/worker-tools.ts .pi/extensions/epub-translate/tests/progress.test.ts
git commit -m "feat: add Pi EPUB translation wizard"
```

## Task 6: Add status commands, reload-safe widget, cancellation, retry, and merge handoff

**Files:**
- Modify: `.pi/extensions/epub-translate/index.ts`
- Modify: `.pi/extensions/epub-translate/worker-tools.ts`
- Modify: `.pi/extensions/epub-translate/tests/test_run_store.py`
- Modify: `.pi/extensions/epub-translate/tests/progress.test.ts`

**Interfaces:**
- `/epub-progress [run]`, `/epub-cancel [run]`, and `/epub-retry [run] [job-id...]` operate by run directory and helper JSON.
- `installProgressWidget(ctx)` reads `summary --run` every two seconds only while an unfinished run exists; it clears its timer/widget in `session_shutdown`.
- `epub_spawn_worker` decides `spawn | merge | exit | cancelled` from a helper transaction; `epub_merge_run` only runs after `try_claim_merge` grants ownership.

- [ ] **Step 1: Write failing recovery and presentation tests**

```python
def test_only_one_worker_can_claim_merge(self):
    store = completed_store(self.tmp)
    self.assertTrue(store.try_claim_merge("worker-a"))
    self.assertFalse(store.try_claim_merge("worker-b"))
```

```ts
test("merging summary takes precedence over active worker counts", () => {
  assert.deepEqual(formatProgress({ state: "running", mergeState: "leased", total: 2,
    done: 2, leased: 0, failed: 0, pending: 0, startedAt: 0, cancelRequested: false }, 0),
    ["EPUB merging · 2/2"]);
});
```

- [ ] **Step 2: Run both test suites and confirm failure**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v && (cd .pi/extensions/epub-translate && npm test)`

Expected: FAIL until the merge lease and formatting branch are implemented.

- [ ] **Step 3: Implement commands and polling cleanup**

```ts
pi.on("session_start", (_event, ctx) => installProgressWidget(ctx));
pi.on("session_shutdown", () => clearInterval(progressTimer));

pi.registerCommand("epub-cancel", {
  description: "Request cancellation of an EPUB translation run",
  handler: async (args, ctx) => {
    await runHelper(ctx, ["cancel", "--run", resolveRunArg(ctx.cwd, args)], undefined);
    await refreshProgressWidget(ctx);
  },
});
```

`/epub-retry` must only reset failed jobs and preserve completed result paths. `epub_spawn_worker` must check cancellation before spawning; `epub_merge_run` must call mechanical merge once and return the output path. On merge failure, set run state `failed` with the validation error and never publish a final output.

- [ ] **Step 4: Run all automated tests**

Run: `python3 -m unittest .pi/extensions/epub-translate/tests/test_run_store.py -v && (cd .pi/extensions/epub-translate && npm test)`

Expected: PASS for cancellation, retry, merge election, all widget states, and prior tests.

- [ ] **Step 5: Manual recovery smoke test with a tiny fixture EPUB**

Run: `mkdir -p /tmp/pi-epub-smoke/input /tmp/pi-epub-smoke/output && cp input/*.epub /tmp/pi-epub-smoke/input/ && pi --approve -e .pi/extensions/epub-translate/index.ts`

Expected: start a run in Pi, observe the widget after `/reload`, use `/epub-cancel`, and confirm no new file appears under `/tmp/pi-epub-smoke/output/`. Do not run a full paid translation in this check.

- [ ] **Step 6: Commit control and UI lifecycle**

```bash
git add .pi/extensions/epub-translate/index.ts .pi/extensions/epub-translate/worker-tools.ts .pi/extensions/epub-translate/tests/test_run_store.py .pi/extensions/epub-translate/tests/progress.test.ts
git commit -m "feat: monitor and recover EPUB translation runs"
```

## Task 7: Verify the auto-discovered extension and retain Codex workflow

**Files:**
- Modify: `.pi/extensions/epub-translate/index.ts` only if verification exposes a load/cleanup defect
- Modify: `.pi/extensions/epub-translate/tests/test_run_store.py` only for a regression found in verification

**Interfaces:**
- `.pi/extensions/epub-translate/index.ts` is automatically discovered from the trusted project without `-e`.
- The original `.agents/skills/translating-epub-books/SKILL.md` and scripts have no diff.

- [ ] **Step 1: Check retained-skill isolation before final verification**

Run: `git diff -- .agents/skills/translating-epub-books && git status --short`

Expected: no diff under `.agents/skills/translating-epub-books`; do not stage the pre-existing untracked `.gitignore`.

- [ ] **Step 2: Run full automated verification**

Run: `python3 -m unittest discover -s .pi/extensions/epub-translate/tests -v && (cd .pi/extensions/epub-translate && npm test) && git diff --check`

Expected: all tests PASS and no whitespace errors.

- [ ] **Step 3: Verify extension auto-discovery in Pi TUI**

Run: `pi --approve`

Expected: `/translate-epub`, `/epub-progress`, `/epub-cancel`, and `/epub-retry` are listed; starting then cancelling the wizard leaves no active timers after `/reload`.

- [ ] **Step 4: Commit only any regression corrections from verification**

```bash
git add .pi/extensions/epub-translate
git commit -m "fix: harden Pi EPUB extension verification"
```

Skip this commit when verification needs no code changes.

## Plan self-review

- **Spec coverage:** Task 1 covers SQLite-only durable state, leases, heartbeats, fencing, retry, cancellation, and merge election. Task 2 covers source preservation, per-document inspection, candidate filtering, XHTML/EPUB mechanics, and verification. Tasks 3 and 6 cover derived persistent progress UI and reload behavior. Task 4 covers fresh stateless Pi workers and self-replacement. Task 5 covers Pi-only model picker, scope review, glossary analysis/editor/lock, and initial seeding. Task 7 verifies local auto-discovery and retained Codex skill.
- **Placeholder scan:** Every task includes commands, concrete test cases, interfaces, and expected output.
- **Type consistency:** `RunSummary` comes from `summary`; `RunStore` owns claim/heartbeat/complete/fail/merge operations; `buildWorkerCommand` and `registerWorkerTools` are consumed by `index.ts`; all worker tools use the same run environment variables and lease token.
