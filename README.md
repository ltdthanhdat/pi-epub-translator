# Pi EPUB Translator

A Pi extension for translating EPUB books while preserving their HTML structure, styling, images, footnotes, and EPUB packaging.

## Requirements

- Pi CLI with a configured model/API
- Node.js and npm
- Python 3.11+
- A local EPUB file

## Install

```bash
git clone https://github.com/ltdthanhdat/pi-epub-translator.git
cd pi-epub-translator
npm --prefix .pi/extensions/epub-translate install
```

## Run in Pi

```bash
pi --approve -e .pi/extensions/epub-translate/index.ts
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
npm --prefix .pi/extensions/epub-translate test
python3 .pi/extensions/epub-translate/tests/test_run_store.py
```

## Repository

This repository is currently private while the workflow is reviewed:
https://github.com/ltdthanhdat/pi-epub-translator
