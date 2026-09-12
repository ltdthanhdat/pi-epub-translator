# Pi EPUB translation extension design

## Goal

Add a project-local Pi extension that translates an EPUB with independent, stateless `pi -p` workers and shows all user interaction and progress in Pi TUI. Keep `.agents/skills/translating-epub-books/` unchanged for Codex users.

The extension owns no durable queue or merge state. A run-local SQLite database is the sole source of truth. The extension creates a run, seeds workers, and renders that database.

## Scope and layout

Add `.pi/extensions/epub-translate/`:

- `index.ts`: Pi commands, wizard, model picker, glossary/scope review, progress widget, and initial worker seeding.
- `worker-tools.ts`: tools available to worker Pi processes: `epub_claim_job`, `epub_complete_job`, `epub_spawn_worker`, and `epub_merge_run`.
- `run-store.py`: mechanical SQLite, XML/EPUB, sampling, and validation helper invoked by the extension tools.
- `tests/`: transaction/recovery and mechanical EPUB tests.

The extension is project-local and uses existing `input/` and `output/` directories. It does not overwrite a source EPUB.

## User flow

`/translate-epub` runs entirely inside Pi TUI:

1. Select an EPUB from `input/`.
2. Select a Pi model from the currently available/scoped model registry and a compatible thinking level.
3. Enter target language and BCP-47 language code, then select worker count.
4. Inspect the EPUB manifest, spine, navigation document, and samples from documents. A stateless Pi analysis process classifies each XHTML document as translatable reading content, notes/footnotes, index-like material, or other supplemental content. The TUI shows every proposed document and lets the user set its inclusion individually. There is no filename-based Index/Notes default.
5. Sample selected content across the book. Deterministically filter candidates by removing stopwords, URLs, page numbers, punctuation-only text, and low-frequency terms; rank proper-name candidates and repeated 2-4 word phrases by frequency and number of documents. A stateless Pi analysis process turns the candidates plus short context into a proposed glossary.
6. Open the glossary in Pi's editor. The user may edit it and chooses **Lock & start**. The locked text is copied into the run and cannot change for that run.
7. The extension prepares job fragments and starts the selected number of Pi workers. Its persistent widget shows run state, done/total, leased, failed, elapsed time, and current merge/completion state.

`/epub-progress` refreshes/shows the current run. `/epub-cancel` sets a durable cancellation flag. `/epub-retry` resets selected or all failed jobs without changing completed results.

## Stateless workers

A worker is a fresh `pi -p` process dedicated to one XHTML fragment. It receives only worker instructions and the run path through environment/configuration; it receives no parent Pi conversation.

1. `epub_claim_job` atomically claims exactly one eligible job from SQLite and returns the XHTML fragment, locked glossary, selected model settings, and a unique `worker_id` plus `lease_token`.
2. The Pi process translates only that fragment, preserving tag/attribute structure and temporary pagebreak markers.
3. `epub_complete_job` validates the fragment, atomically publishes its result file, and marks the job done only when its `worker_id` and `lease_token` still match.
4. If jobs remain, `epub_spawn_worker` starts one replacement `pi -p` process and the current worker exits. Thus N seeded chains remain active without an orchestrator loop.
5. If all jobs are done, `epub_merge_run` atomically claims the merge state. Its single winner merges, builds, verifies, and publishes the EPUB. Other workers exit.

## Concurrency and recovery

SQLite serializes the job-claim `UPDATE ... RETURNING`, so only one process receives a pending fragment. After a claim, the worker extension starts a background heartbeat that renews the lease only if both `worker_id` and `lease_token` match. It stops on completion or session shutdown.

If a worker crashes, its heartbeat stops. After its lease expires another worker may claim it and increments `attempts`. Completion is fenced by the lease token, so a stale worker cannot publish or mark done after losing its lease. Retry stops at configurable `max_attempts`; a run with terminal failures remains failed until `/epub-retry`.

Cancellation is checked before claim, completion, respawn, and merge. It never publishes a partial output EPUB.

## Merge and verification

The merge winner works in a staging directory:

1. Copy the extracted source EPUB and replace each selected fragment by its result using its stored locator.
2. Update `lang` and `xml:lang` only on translated XHTML documents.
3. Build with `mimetype` first and stored/uncompressed.
4. Verify archive integrity, exact intended member set, each replacement's XHTML tag/attribute structure, and removal of temporary pagebreak markers. Run `epubcheck` when installed.
5. Atomically rename the finished EPUB to `output/<book>-<target-language>.epub`, then mark the run completed.

## UI state

The TUI widget is derived solely from SQLite and is restored after `/reload` or a new Pi session. It is advisory only: it never controls job assignment or merge. Detailed failed-job information is available through the progress command.

## Testing

Add coverage for:

- concurrent claims returning distinct jobs;
- lease heartbeat and stale-token fencing;
- crash/lease expiry/retry and retry limits;
- only one merge winner;
- cancel/resume/reload behavior;
- document-scope classification inputs and glossary candidate filtering;
- fragment structure validation, pagebreak restoration, ZIP layout/member verification, and optional `epubcheck` invocation;
- progress-widget snapshots from SQLite states.

## Non-goals

- No web UI or external application.
- No migration/removal of the existing Codex skill.
- No persistent Pi worker or shared worker conversation.
- No automatic translation-scope decision without user review.
