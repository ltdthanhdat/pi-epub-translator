# Parallel EPUB Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, parallel EPUB translator that runs Codex or Claude CLI workers and writes a separate EPUB from books in `input/`.

**Architecture:** One stdlib Python coordinator owns an SQLite run database and all XHTML merging. It splits XHTML at complete terminal text-bearing block elements, leases jobs to short-lived CLI subprocesses, validates each result against its source structure, and rebuilds only after every job finishes.

**Tech Stack:** Python 3 stdlib (`argparse`, `sqlite3`, `subprocess`, `xml.etree.ElementTree`, `unittest`), installed `codex`/`claude` CLIs, existing `epub_tool.py`.

**Spec:** `docs/superpowers/specs/2026-09-12-parallel-epub-translation-design.md`

## Global Constraints

- Discover input EPUBs only from `input/` and write outputs only to `output/`; never overwrite an input.
- Use local SQLite and files only; add no dependency, server, or tmux integration.
- Prompt for backend, model, target language, and worker count before starting work.
- Show a numbered configured model list and an explicit custom-model option; persist the chosen backend/model per job.
- Workers never write extracted XHTML or the output EPUB.
- Treat `epub:type="pagebreak"` as content, not a job boundary.
- The current directory is not a Git repository; do not run commit commands.

---

### Task 1: Define the local run layout and SQLite lease store

**Files:**
- Create: `scripts/parallel_translate.py`
- Create: `scripts/test_parallel_translate.py`

**Interfaces:**
- Produces: `RunStore(db_path: Path)`, `RunStore.add_jobs(jobs)`, `RunStore.lease(worker_id, now) -> Job | None`, `RunStore.complete(job_id, result_path)`, `RunStore.fail(job_id, message)`, and `RunStore.all_done() -> bool`.
- Produces: `Job(id: int, xhtml_path: str, locator: str, input_path: str, result_path: str | None, backend: str, model: str, target_language: str, attempts: int)`.

- [ ] **Step 1: Write the failing lease test**

```python
class RunStoreTest(unittest.TestCase):
    def test_expired_lease_is_reissued_once(self):
        store = RunStore(self.tmp / "run.sqlite")
        store.add_jobs([job("one")])
        first = store.lease("worker-a", now=100)
        self.assertEqual("one", first.locator)
        self.assertIsNone(store.lease("worker-b", now=101))
        second = store.lease("worker-b", now=100 + LEASE_SECONDS + 1)
        self.assertEqual(first.id, second.id)
        self.assertEqual(2, second.attempts)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: FAIL because `RunStore` and `LEASE_SECONDS` do not exist.

- [ ] **Step 3: Implement the minimal run store**

```python
LEASE_SECONDS = 900

class RunStore:
    def lease(self, worker_id: str, now: int) -> Job | None:
        with self.connection() as db:
            row = db.execute(
                """UPDATE jobs SET state='leased', worker_id=?, lease_until=?,
                   attempts=attempts+1
                   WHERE id=(SELECT id FROM jobs WHERE state='pending' OR
                     (state='leased' AND lease_until < ?) ORDER BY id LIMIT 1)
                   RETURNING *""",
                (worker_id, now + LEASE_SECONDS, now),
            ).fetchone()
        return Job.from_row(row) if row else None
```

Create the `jobs` table with the fields specified by the design, a `runs` table for selected settings, and atomic `complete`/`fail` updates that only accept a currently leased job. Use `sqlite3.Row` and a new connection per operation.

- [ ] **Step 4: Run the store test**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: PASS, including lease expiry and one-time completion assertions.

### Task 2: Prepare safe XHTML fragments from an EPUB workspace

**Files:**
- Modify: `scripts/parallel_translate.py`
- Modify: `scripts/test_parallel_translate.py`

**Interfaces:**
- Consumes: `extract_epub(source: Path, workspace: Path)` and OPF spine XHTML entries.
- Produces: `prepare_jobs(workspace: Path, settings: Settings) -> list[JobDraft]`, where every draft has one full block element encoded in `input/<id>.xhtml` and a numeric child-index locator.
- Produces: `validate_fragment(source: str, translated: str) -> None`, raising `ValueError` if element/tag/attribute structure differs.

- [ ] **Step 1: Write failing preparation and validation tests**

```python
def test_prepare_keeps_pagebreak_inside_one_block(self):
    write_book(self.tmp, "<body><section><p>A<span epub:type='pagebreak'/>B</p><p>C</p></section></body>")
    jobs = prepare_jobs(self.tmp, settings())
    self.assertEqual(2, len(jobs))
    self.assertIn("pagebreak", Path(jobs[0].input_path).read_text())

def test_validate_fragment_rejects_changed_href(self):
    with self.assertRaisesRegex(ValueError, "structure"):
        validate_fragment('<p><a href="n">text</a></p>', '<p><a href="x">dịch</a></p>')
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: FAIL because preparation and validation functions do not exist.

- [ ] **Step 3: Implement the smallest safe splitter**

Read the OPF manifest/spine with `ElementTree`, resolve only XHTML spine entries, and recursively select non-overlapping terminal text-bearing elements (`p`, headings, captions, list items, table cells, and text-only `div`/`span` leaves). Do not select a container that has a selected descendant. Use a child-index path from the document root rather than source byte offsets. Serialize each complete element as a fragment under the run workspace. Do not split a block containing `pagebreak`.

Implement validation by parsing source/result fragments and recursively comparing tag names, attributes, child counts, and child order while ignoring only `.text` and `.tail`. Reject empty results.

- [ ] **Step 4: Run preparation tests**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: PASS; a pagebreak remains in one job and attribute mutation is rejected.

### Task 3: Add backend/model selection and worker execution

**Files:**
- Modify: `scripts/parallel_translate.py`
- Modify: `scripts/test_parallel_translate.py`

**Interfaces:**
- Produces: `choose_settings(input_fn=input) -> Settings` with `backend`, `model`, `target_language`, and `workers` fields.
- Produces: `build_command(job: Job) -> list[str]`.
- Produces: `run_worker(store: RunStore, worker_id: str) -> None`.

- [ ] **Step 1: Write failing menu and command tests**

```python
def test_choose_settings_accepts_numbered_model(self):
    answers = iter(["1", "2", "Vietnamese", "3"])
    chosen = choose_settings(lambda _: next(answers))
    self.assertEqual("codex", chosen.backend)
    self.assertEqual(CODEX_MODELS[1], chosen.model)
    self.assertEqual(3, chosen.workers)

def test_build_command_uses_noninteractive_model_flag(self):
    command = build_command(job(backend="claude", model="sonnet"))
    self.assertEqual(["claude", "-p", "--model", "sonnet"], command[:4])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: FAIL because selection and command functions do not exist.

- [ ] **Step 3: Implement interactive selection and subprocess worker**

Keep `CODEX_MODELS` and `CLAUDE_MODELS` as short, editable tuples near the script top; print them as numbered options plus `0) custom model`. Include the provider default as the first option. Do not claim this is an account-wide catalog because neither installed CLI exposes one.

`build_command` must build `codex exec --model MODEL PROMPT` or `claude -p --model MODEL PROMPT`. The prompt supplies the source fragment, target language, glossary path when present, and requires translated fragment only. `run_worker` leases one job, invokes the command with `subprocess.run(..., capture_output=True, text=True)`, writes stdout atomically to the job result path, validates it, then completes or fails the job with stderr in the error field.

- [ ] **Step 4: Run selection and command tests**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: PASS; selected model propagates to the worker command.

### Task 4: Merge validated results, rebuild, and expose the command-line flow

**Files:**
- Modify: `scripts/parallel_translate.py`
- Modify: `scripts/test_parallel_translate.py`
- Modify: `SKILL.md`

**Interfaces:**
- Produces: `merge_run(workspace: Path, store: RunStore, output_epub: Path) -> None`.
- Produces: `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write failing merge and input-directory tests**

```python
def test_merge_changes_only_completed_fragment_and_builds_output(self):
    store, workspace = prepared_run(self.tmp)
    complete_first_job(store, "<p>Xin chào</p>")
    complete_remaining_jobs(store)
    merge_run(workspace, store, self.tmp / "output" / "translated.epub")
    self.assertTrue((self.tmp / "output" / "translated.epub").is_file())
    self.assertEqual(original_input_bytes, (self.tmp / "input" / "book.epub").read_bytes())

def test_main_rejects_missing_input_directory(self):
    self.assertEqual(2, main(["--root", str(self.tmp)]))
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v`

Expected: FAIL because merge and CLI flow do not exist.

- [ ] **Step 3: Implement merge and CLI**

Make `main` require `ROOT/input/*.epub`, offer a numbered source-book menu, create `ROOT/output/` only when a run starts, and reject an existing target filename. Start the requested number of local worker processes and wait for them. On completed work, locate each original block by child-index path, replace it with the validated result element, set root XHTML `lang` and `xml:lang` to the target language, and call the existing `epub_tool.py build` helper.

If jobs are still pending or failed, print the run database path and return nonzero without building. If the same run directory exists, offer resume instead of creating duplicate jobs.

Update `SKILL.md` with the `input/`/`output/` convention, preflight model/backend question, command to run the coordinator, resume behavior, and the fact that tmux is deliberately unsupported.

- [ ] **Step 4: Run all checks and one syntax check**

Run: `python3 -m unittest scripts/test_parallel_translate.py -v && python3 -m py_compile scripts/parallel_translate.py`

Expected: PASS and no output EPUB is built until every job is done.

## Plan self-review

- Spec coverage: Task 1 covers SQLite leases/recovery; Task 2 covers spine/block splitting and structural protection; Task 3 covers backend/model choice plus parallel CLI workers; Task 4 covers `input/`, safe output, merge/build, resume UI, and skill documentation.
- Placeholder scan: no TBD/TODO or unspecified validation remains.
- Type consistency: `Job`, `Settings`, `RunStore`, `prepare_jobs`, `validate_fragment`, `build_command`, `run_worker`, `merge_run`, and `main` are introduced before use by later tasks.
