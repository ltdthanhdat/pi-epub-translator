# Pi EPUB Translator

A Pi extension for translating EPUB books while preserving their HTML structure, styling, images, footnotes, and EPUB packaging.

## Requirements

- Pi CLI with a configured model/API
- Node.js and npm
- Python 3.11+
- A local EPUB file

## Install

Install the published Pi package:

```bash
pi install npm:pi-epub-translator
```

For local development:

```bash
git clone https://github.com/ltdthanhdat/pi-epub-translator.git
cd pi-epub-translator
npm install
```

## Run in Pi

```bash
pi --approve -e ./src/index.ts
```

Use `/translate-epub` to choose the EPUB, model, thinking level, target language, and worker count. The wizard then lets you review the AI document scope and glossary before starting.

## Monitor and control a run

- `/epub-progress` — show SQLite-backed progress
- `/epub-cancel` — request cancellation
- `/epub-retry` — retry an eligible failed or cancelled run

Workers use fresh no-session Pi processes. SQLite leases and fencing prevent duplicate fragment claims; the final worker merges and verifies the EPUB.

## Local files

Put EPUB inputs in `input/`. Generated outputs and run state live in ignored local directories. Do not commit books, translated output, credentials, or `.parallel-translate/` state.

## Test

```bash
npm test
python3 tests/test_run_store.py
```

## Release

1. Increment `version` in `package.json`.
2. Run the tests and `npm pack --dry-run`.
3. Publish with `npm publish --access public`.
4. Verify with `npm view pi-epub-translator version` and a clean `pi install npm:pi-epub-translator`.

## Repository

https://github.com/ltdthanhdat/pi-epub-translator
