# Parallel EPUB translation

## Goal

Translate an EPUB concurrently with either Codex or Claude, resume safely after interruption, and build a separate translated EPUB without concurrent writes to source XHTML.

## Run flow

`parallel_translate.py` discovers source books from `input/` and asks the user to choose one. It writes the translated EPUB to `output/`; neither directory is overwritten implicitly. It then asks, in order, for:

1. backend (`codex` or `claude`);
2. a numbered model choice for that backend, plus an option to enter another model name;
3. target language and worker count.

The selected backend/model is stored with every job. A resumed run keeps those values rather than silently changing model.

## Work units and state

The coordinator extracts the EPUB into a run workspace and reads the OPF spine. It recursively selects non-overlapping terminal text-bearing block elements (such as paragraphs and headings), rather than direct `body` children, because chapters can be wrapped in a single `section`. `epub:type="pagebreak"` is retained as content metadata but is not a split boundary because it can occur inside a paragraph.

SQLite is the source of truth. Each job has an id, source locator, input/result paths, selected backend/model, state (`pending`, `leased`, `done`, `failed`), attempt count, lease expiry, and last error. A worker atomically leases one pending or expired job, writes only its own result file, and marks it done. The coordinator is the only process that merges validated results and writes the output EPUB.

## Worker contract

Workers run as short-lived `codex exec --model ...` or `claude -p --model ...` subprocesses. The prompt requires returning the translated fragment only, preserving markup and attributes. A result is rejected if parsing or structural comparison shows changed tags/attributes; rejected jobs retain the error and can be retried.

## Completion and recovery

On restart, expired leases return to `pending`; completed results are never translated again. The build step runs only when every required job is `done`, updates XHTML language attributes to the target language, and uses the existing EPUB build helper. It never overwrites the input EPUB.

## Scope and checks

The initial version uses local `sqlite3`, `subprocess`, and the existing `epub_tool.py`; no queue service, tmux integration, or new dependency. A small self-check will verify lease recovery, one-time completion, and that merge refuses a structurally changed fragment.
