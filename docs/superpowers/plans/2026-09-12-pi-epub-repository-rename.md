# Pi EPUB Translator Repository Cleanup and Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the reusable skill to `$HOME/.agents/skills`, reduce the repository to the Pi extension plus README/config, and replace the existing private GitHub repository history with one clean root commit under `ltdthanhdat/pi-epub-translator`.

**Architecture:** First update the user-level skill and product README in an isolated translator worktree. Then create a local recovery bundle and a clean Git snapshot containing only `.pi/extensions/epub-translate/`, `.gitignore`, and `README.md`. Rename the existing private GitHub repository in place and guarded-force-update only its `main` branch with an explicit expected-old-SHA lease.

**Tech Stack:** Git, GitHub CLI (`gh`), GitHub SSH remote, Python, Node.js, Pi CLI.

**Spec:** `docs/superpowers/specs/2026-09-12-pi-epub-translation-blog-design.md`

## Global Constraints

- Rename the existing repository; do not create a second repository.
- Keep `ltdthanhdat/pi-epub-translator` private.
- Replace remote `main` only after a local recovery bundle and expected-old-SHA check exist.
- Use `--force-with-lease=<expected remote SHA>`, never unguarded `--force`.
- Do not stage or destroy unrelated changes in `/home/datlt/workspace/translator/.gitignore`.
- Do not include `input/`, `output/`, `.parallel-translate/`, `.worktrees/`, EPUBs, or generated artifacts.
- The final remote tree contains only `.pi/extensions/epub-translate/`, `.gitignore`, and `README.md`.

---

### Task 1: Migrate the general EPUB skill to the user-level skills directory

**Files:**
- Source: `/home/datlt/workspace/translator/.agents/skills/translating-epub-books/`
- Destination: `/home/datlt/.agents/skills/translating-epub-books/`
- Modify destination only: `/home/datlt/.agents/skills/translating-epub-books/SKILL.md`

**Interfaces:**
- Consumes: the currently tracked repository skill.
- Produces: a user-level skill whose command examples work outside the repository.

- [ ] **Step 1: Copy the skill before deleting the repository copy**

```bash
ROOT=/home/datlt/workspace/translator
DEST=/home/datlt/.agents/skills/translating-epub-books
rm -rf "$DEST"
mkdir -p "$HOME/.agents/skills"
cp -a "$ROOT/.agents/skills/translating-epub-books" "$HOME/.agents/skills/"
```

Expected: all four skill files exist under `$HOME/.agents/skills/translating-epub-books`.

- [ ] **Step 2: Update repository-relative script paths in the migrated skill**

```bash
SKILL=/home/datlt/.agents/skills/translating-epub-books/SKILL.md
python3 - "$SKILL" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = ".agents/skills/translating-epub-books/"
new = "$HOME/.agents/skills/translating-epub-books/"
if old not in text:
    raise SystemExit(f"expected path not found in {path}")
path.write_text(text.replace(old, new))
PY
rg -n '\$HOME/\.agents/skills/translating-epub-books' "$SKILL"
```

Expected: every command example points to `$HOME/.agents/skills/translating-epub-books`; no repository-relative skill path remains.

- [ ] **Step 3: Run the migrated skill's tests from its new location**

```bash
cd /home/datlt/.agents/skills/translating-epub-books/scripts
python3 test_parallel_translate.py
```

Expected: all existing skill tests pass.

- [ ] **Step 4: Remove the repository copy in the isolated translator worktree**

```bash
rm -rf /home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog/.agents
```

Expected: the isolated product tree no longer contains `.agents/`; the user-level copy remains intact.

### Task 2: Add the standalone extension README

**Files:**
- Create: `/home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog/README.md`

**Interfaces:**
- Consumes: the existing Pi extension commands and package metadata.
- Produces: installation and usage documentation for a fresh checkout.

- [ ] **Step 1: Write the README with the approved product scope**

The README must contain these sections and commands:

```markdown
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
```

Do not mention `@injaneity/pi-computer-use`, the removed general-purpose skill, or any screenshot tooling.

- [ ] **Step 2: Validate README commands and references**

```bash
README=/home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog/README.md
rg -n '^# Pi EPUB Translator|npm --prefix|pi --approve -e|/translate-epub|/epub-progress|/epub-cancel|/epub-retry|pi-epub-translator' "$README"
! rg -ni 'computer-use|injaneity' "$README"
```

Expected: all required sections/commands are present and the capture tool is absent.

### Task 3: Build and verify the clean repository snapshot

**Files:**
- Read: `/home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog/.pi/extensions/epub-translate/`
- Include: `.gitignore`, `README.md`
- Temporary recovery bundle: `/tmp/pi-epub-translator-before-clean.bundle`
- Temporary clean snapshot repository: `/tmp/pi-epub-translator-clean/`

**Interfaces:**
- Consumes: the migrated skill, README, and existing extension source/tests.
- Produces: one clean root commit containing only the approved product tree and a local recovery bundle for rollback.

- [ ] **Step 1: Record the current remote and create a recovery bundle**

```bash
ROOT=/home/datlt/workspace/translator
REMOTE_SHA=$(git ls-remote https://github.com/ltdthanhdat/epub-translator.git refs/heads/main | awk '{print $1}')
test -n "$REMOTE_SHA"
printf '%s\n' "$REMOTE_SHA" > /tmp/pi-epub-translator-remote-main.sha
git -C "$ROOT" bundle create /tmp/pi-epub-translator-before-clean.bundle --all
git bundle verify /tmp/pi-epub-translator-before-clean.bundle
```

Expected: the bundle verifies and the expected current remote SHA is saved. Remove the leading space before `git` if copying the command literally into a shell.

- [ ] **Step 2: Create a temporary clean Git repository from approved files only**

```bash
ROOT=/home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog
CLEAN=/tmp/pi-epub-translator-clean
rm -rf "$CLEAN"
mkdir -p "$CLEAN"
cp -a "$ROOT/.pi" "$CLEAN/.pi"
cp "$ROOT/README.md" "$CLEAN/README.md"
cp /home/datlt/workspace/translator/.gitignore "$CLEAN/.gitignore"
git -C "$CLEAN" init -b main
git -C "$CLEAN" add .pi .gitignore README.md
git -C "$CLEAN" commit -m "Initial import: Pi EPUB translator"
```

Expected: the clean repository has one root commit and only `.pi/`, `.gitignore`, and `README.md` in its tree. The current local `.gitignore` is copied into the temporary snapshot but is not staged in the original checkout.

- [ ] **Step 3: Verify the clean tree before any remote mutation**

```bash
git -C /tmp/pi-epub-translator-clean ls-tree -r --name-only HEAD
COUNT=$(git -C /tmp/pi-epub-translator-clean rev-list --count HEAD)
test "$COUNT" -eq 1
test "$(git -C /tmp/pi-epub-translator-clean ls-tree -r --name-only HEAD | grep -c '^\.pi/extensions/epub-translate/')" -gt 0
! git -C /tmp/pi-epub-translator-clean ls-tree -r --name-only HEAD | rg '^(\.agents|docs|input|output|\.parallel-translate)/'
```

Expected: exactly one commit; extension files, `.gitignore`, and `README.md` are present; `.agents`, `docs`, EPUBs, and generated state are absent.

### Task 4: Rename the private repository and guarded-force-push the clean history

**Files:**
- Remote only: `ltdthanhdat/epub-translator` → `ltdthanhdat/pi-epub-translator`
- Remote branch only: replace `main` with the clean root commit

**Interfaces:**
- Consumes: `/tmp/pi-epub-translator-clean`, `/tmp/pi-epub-translator-remote-main.sha`, and the verified current private repository.
- Produces: a private renamed repository with one clean `main` commit.

- [ ] **Step 1: Rename the existing repository in place**

```bash
gh repo rename pi-epub-translator --repo ltdthanhdat/epub-translator --yes
gh repo view ltdthanhdat/pi-epub-translator --json name,visibility,defaultBranchRef,url
```

Expected: URL is `https://github.com/ltdthanhdat/pi-epub-translator`, visibility is `PRIVATE`, and no second repository exists.

- [ ] **Step 2: Point the temporary clean repository at the renamed remote**

```bash
git -C /tmp/pi-epub-translator-clean remote add origin git@github.com:ltdthanhdat/pi-epub-translator.git
git -C /tmp/pi-epub-translator-clean remote -v
```

Expected: the temporary repository has the renamed SSH URL for fetch and push.

- [ ] **Step 3: Replace only the expected remote `main` commit**

```bash
EXPECTED=$(cat /tmp/pi-epub-translator-remote-main.sha)
ACTUAL=$(git ls-remote git@github.com:ltdthanhdat/pi-epub-translator.git refs/heads/main | awk '{print $1}')
test "$ACTUAL" = "$EXPECTED"
git -C /tmp/pi-epub-translator-clean push --force-with-lease=refs/heads/main:$EXPECTED origin HEAD:main
```

Expected: push succeeds only if remote `main` has not changed since the preflight. No unguarded `--force` is allowed.

- [ ] **Step 4: Ensure `main` is the default branch and keep private visibility**

```bash
gh repo edit ltdthanhdat/pi-epub-translator --default-branch main
gh repo view ltdthanhdat/pi-epub-translator --json name,visibility,defaultBranchRef,url
```

Expected: `main` is default and visibility remains `PRIVATE`.

### Task 5: Verify the clean remote and local safety

**Files:**
- Read only: `/home/datlt/workspace/translator`
- Read only: `/tmp/pi-epub-translator-clean`

**Interfaces:**
- Consumes: the renamed private remote and recovery bundle.
- Produces: evidence that the remote is clean and that the original local working tree was not destructively reset.

- [ ] **Step 1: Verify remote commit count and tree**

```bash
REMOTE_HEAD=$(git ls-remote git@github.com:ltdthanhdat/pi-epub-translator.git refs/heads/main | awk '{print $1}')
test -n "$REMOTE_HEAD"
test "$(git -C /tmp/pi-epub-translator-clean rev-parse HEAD)" = "$REMOTE_HEAD"
git -C /tmp/pi-epub-translator-clean ls-tree -r --name-only HEAD
```

Expected: remote `main` equals the clean root commit and contains only the approved product tree.

- [ ] **Step 2: Verify local recovery artifact and original checkout state**

```bash
test -s /tmp/pi-epub-translator-before-clean.bundle
git -C /home/datlt/workspace/translator status --short --branch
git -C /home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog status --short --branch
```

Expected: recovery bundle exists; the original `.gitignore` modification is not silently discarded; no EPUB or generated artifact is staged.

- [ ] **Step 3: Keep the remote private for review**

```bash
test "$(gh repo view ltdthanhdat/pi-epub-translator --json visibility --jq '.visibility')" = PRIVATE
```

Expected: command exits 0.

---

## Final verification

Run after all tasks:

```bash
gh repo view ltdthanhdat/pi-epub-translator --json name,visibility,defaultBranchRef,url
REMOTE_HEAD=$(git ls-remote git@github.com:ltdthanhdat/pi-epub-translator.git refs/heads/main | awk '{print $1}')
test -n "$REMOTE_HEAD"
test "$(git -C /tmp/pi-epub-translator-clean rev-parse HEAD)" = "$REMOTE_HEAD"
test "$(git -C /tmp/pi-epub-translator-clean rev-list --count HEAD)" -eq 1
```

The final output must show the renamed private repository, default branch `main`, and one clean commit. Keep `/tmp/pi-epub-translator-before-clean.bundle` until the user approves the remote cleanup.
