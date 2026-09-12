# Pi EPUB Translation Blog Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture truthful screenshots of the existing Pi EPUB translation workflow and create an English Obsidian blog draft that documents it without mentioning the screenshot tool.

**Architecture:** Use the installed computer-use Pi package only as a temporary operator for the real local TUI session. Keep screenshots in a staging directory until selected, then copy five assets and one Markdown post into the canonical Obsidian blog source. Validate through the same blog projection command used by CI while leaving the draft unpublished and preserving all pre-existing Obsidian changes.

**Tech Stack:** Pi, `@injaneity/pi-computer-use`, EPUB sample, TypeScript extension, Markdown frontmatter, pnpm blog tooling, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-09-12-pi-epub-translation-blog-design.md`

## Global Constraints

- The article title is exactly `I Created a Pi Workflow to Translate EPUB Books Without Losing Their Structure`.
- The article must use `status: draft`.
- The computer-use package is a capture utility only; do not mention or link it in the article.
- Use the private repository link `https://github.com/ltdthanhdat/pi-epub-translator`.
- Do not copy or commit EPUB source files, translated output, run state, credentials, or long copyrighted passages.
- Do not modify Obsidian workflow files, raw knowledge files, or unrelated existing dirty files.
- Do not commit or push Obsidian changes unless explicitly requested; leave the new draft and assets reviewable in the working tree.

---

### Task 1: Capture the Obsidian baseline and prepare a private screenshot staging area

**Files:**
- Read only: `/home/datlt/workspace/obsidian`
- Create outside Git repositories: `/tmp/pi-epub-translation-workflow/`

**Interfaces:**
- Consumes: the current dirty Obsidian working tree and the approved article asset names.
- Produces: a baseline status file and an empty private staging directory.

- [ ] **Step 1: Record the existing Obsidian status**

```bash
OBSIDIAN=/home/datlt/workspace/obsidian
BASELINE=/tmp/obsidian-status-before-pi-epub-blog.txt
git -C "$OBSIDIAN" status --short --untracked-files=all > "$BASELINE"
printf 'Recorded baseline at %s\n' "$BASELINE"
```

Expected: the baseline contains the existing workspace/dashboard modifications, deletions, and `.trash/`; it is not edited.

- [ ] **Step 2: Create a staging directory outside both repositories**

```bash
rm -rf /tmp/pi-epub-translation-workflow
mkdir -p /tmp/pi-epub-translation-workflow
```

Expected: `/tmp/pi-epub-translation-workflow` exists and contains no repository files.

- [ ] **Step 3: Install the capture-only Pi package**

```bash
pi install npm:@injaneity/pi-computer-use
```

Expected: Pi reports a successful package installation. This changes Pi's local installation only; it does not add a dependency to the translator or blog repository.

### Task 2: Run the real sample workflow and capture five truthful states

**Files:**
- Create in staging: `/tmp/pi-epub-translation-workflow/01-choose-epub.png`
- Create in staging: `/tmp/pi-epub-translation-workflow/02-model-effort-workers.png`
- Create in staging: `/tmp/pi-epub-translation-workflow/03-ai-inspection.png`
- Create in staging: `/tmp/pi-epub-translation-workflow/04-scope-glossary-review.png`
- Create in staging: `/tmp/pi-epub-translation-workflow/05-translation-progress.png`

**Interfaces:**
- Consumes: `/home/datlt/workspace/translator/input/Vietnam - A New History - 10-page-sample.epub`, the Pi extension, and the installed capture tool.
- Produces: screenshots showing actual UI states, with no secrets or long source excerpts.

- [ ] **Step 1: Make the local sample visible to the isolated translator worktree without tracking it**

```bash
TRANSLATOR=/home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog
if [ ! -e "$TRANSLATOR/input" ]; then
  ln -s /home/datlt/workspace/translator/input "$TRANSLATOR/input"
fi
test -f "$TRANSLATOR/input/Vietnam - A New History - 10-page-sample.epub"
```

Expected: the extension can resolve the ignored sample through the worktree-local `input` path; no tracked file is created.

- [ ] **Step 2: Start the extension in Pi**

```bash
cd /home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog
pi --approve -e .pi/extensions/epub-translate/index.ts
```

Expected: Pi starts with the `epub-translate` extension and shows no extension-load error.

- [ ] **Step 3: Capture the EPUB selection state**

Use the installed computer-use operator to invoke `/translate-epub`, select `Vietnam - A New History - 10-page-sample.epub`, and capture the visible EPUB selection screen as:

```text
/tmp/pi-epub-translation-workflow/01-choose-epub.png
```

Expected: the image shows the local workflow and filename but no credentials or unrelated personal data.

- [ ] **Step 4: Capture model, effort, and worker choices**

Continue the same real wizard, keep the selected model and effort visible, choose a small worker count suitable for the sample, and capture:

```text
/tmp/pi-epub-translation-workflow/02-model-effort-workers.png
```

Expected: the image shows that the user controls model, thinking/effort, and worker count.

- [ ] **Step 5: Capture the blocking AI inspection state**

Allow EPUB inspection and AI scope/glossary analysis to run. During the blocking loader state, capture:

```text
/tmp/pi-epub-translation-workflow/03-ai-inspection.png
```

Expected: the loader is visible and input is blocked; do not claim completion from this image.

- [ ] **Step 6: Capture scope and glossary review**

When the wizard reaches review, capture the screen showing the AI-classified translate/keep summary and the glossary editor as:

```text
/tmp/pi-epub-translation-workflow/04-scope-glossary-review.png
```

Expected: the image demonstrates human review before Lock & start. Crop or redact long book excerpts if they are visible.

- [ ] **Step 7: Capture progress only after a real run begins**

Lock the reviewed scope and start the sample run if a configured model/API is available. Capture the progress state as:

```text
/tmp/pi-epub-translation-workflow/05-translation-progress.png
```

Expected: the image shows actual progress or an actual terminal state. If a real translation cannot start or complete, keep the truthful progress/error state and state the limitation in the article; never fabricate a completed output screenshot.

### Task 3: Create the canonical Obsidian blog draft and copy selected assets

**Files:**
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/posts/pi-epub-translation-workflow.md`
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/01-choose-epub.png`
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/02-model-effort-workers.png`
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/03-ai-inspection.png`
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/04-scope-glossary-review.png`
- Create: `/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/05-translation-progress.png`

**Interfaces:**
- Consumes: the five selected staging screenshots and repository URL from the repository-rename plan.
- Produces: one draft Markdown post and five blog assets in the canonical source tree.

- [ ] **Step 1: Copy only the five selected screenshots**

```bash
OBSIDIAN=/home/datlt/workspace/obsidian
ASSETS="$OBSIDIAN/20-areas/writing/blog/assets/pi-epub-translation-workflow"
mkdir -p "$ASSETS"
for image in \
  01-choose-epub.png \
  02-model-effort-workers.png \
  03-ai-inspection.png \
  04-scope-glossary-review.png \
  05-translation-progress.png; do
  test -s "/tmp/pi-epub-translation-workflow/$image"
  cp "/tmp/pi-epub-translation-workflow/$image" "$ASSETS/$image"
done
```

Expected: exactly five non-empty PNG files exist under the article asset directory.

- [ ] **Step 2: Write the required frontmatter**

The post must start with this exact frontmatter:

```yaml
---
title: "I Created a Pi Workflow to Translate EPUB Books Without Losing Their Structure"
slug: pi-epub-translation-workflow
description: "How I built a Pi-powered EPUB translation workflow that preserves book structure while adding AI-assisted scope, glossary, retries, and progress tracking."
status: draft
date: 2026-09-12
tags:
  - ai
  - pi
  - epub
  - translation
  - automation
---
```

Expected: the post passes the source format documented in `20-areas/writing/blog/README.md`.

- [ ] **Step 3: Write the article sections in first-person English**

Write 1,200–1,800 words with these headings and facts:

```text
## The problem: translating an EPUB is not just translating text
## The project
## The translation workflow
### Choosing the EPUB
### Choosing the model, effort, and workers
### Letting AI classify the document scope
### Reviewing the glossary
### Starting workers and monitoring progress
## What happens under the hood
## Why human review stays in the loop
## What worked and what is still limited
## Conclusion
```

Include the repository link exactly once in the project section and once in the conclusion if natural:

```markdown
[pi-epub-translator](https://github.com/ltdthanhdat/pi-epub-translator)
```

Embed the five images using the existing Obsidian syntax, with captions describing the UI state. Do not mention `computer-use`, `injaneity`, or the screenshot installation process.

- [ ] **Step 4: Check the post for forbidden content**

```bash
POST=/home/datlt/workspace/obsidian/20-areas/writing/blog/posts/pi-epub-translation-workflow.md
! rg -ni 'computer-use|injaneity|api[_ -]?key|secret|password' "$POST"
rg -n '^title:|^slug:|^description:|^status:|^date:|^tags:' "$POST"
```

Expected: the forbidden-content check returns no matches; all required frontmatter keys are present and `status: draft` is exact.

### Task 4: Validate the draft through the existing blog projection flow

**Files:**
- Read only: `/home/datlt/workspace/obsidian/.github/workflows/ci.yml`
- Read only: `/home/datlt/workspace/obsidian/.github/workflows/publish-blog.yml`
- Temporary clone outside the repositories: `/tmp/pi-epub-blog-projection/`

**Interfaces:**
- Consumes: the canonical Obsidian blog source and private `ltdthanhdat/blog` repository.
- Produces: a passing local equivalent of `publish:sync` and `pnpm run ci`, without committing or pushing the generated projection.

- [ ] **Step 1: Clone the private blog projection into a temporary directory**

```bash
rm -rf /tmp/pi-epub-blog-projection
gh repo clone ltdthanhdat/blog /tmp/pi-epub-blog-projection
```

Expected: the private blog repository is cloned outside `~/workspace/obsidian`.

- [ ] **Step 2: Install the blog projection dependencies exactly as CI does**

```bash
pnpm --dir /tmp/pi-epub-blog-projection install --frozen-lockfile
```

Expected: dependency installation succeeds with the committed lockfile.

- [ ] **Step 3: Run the canonical sync and CI commands**

```bash
pnpm --dir /tmp/pi-epub-blog-projection publish:sync \
  --source-root /home/datlt/workspace/obsidian/20-areas/writing/blog \
  --blog-root /tmp/pi-epub-blog-projection
pnpm --dir /tmp/pi-epub-blog-projection run ci
```

Expected: sync and static validation/build pass. The draft is not published as public content; no generated files are copied back into Obsidian.

- [ ] **Step 4: Remove the temporary projection clone**

```bash
rm -rf /tmp/pi-epub-blog-projection
```

Expected: no temporary blog clone remains.

### Task 5: Verify scope isolation and hand off the draft

**Files:**
- Read only: `/tmp/obsidian-status-before-pi-epub-blog.txt`
- Read only: `/home/datlt/workspace/obsidian`

**Interfaces:**
- Consumes: the baseline, new post, new assets, and validation output.
- Produces: a reviewable draft with no unrelated changes staged or overwritten.

- [ ] **Step 1: Confirm only the intended new paths were added by this work**

```bash
OBSIDIAN=/home/datlt/workspace/obsidian
git -C "$OBSIDIAN" status --short --untracked-files=all
printf '%s\n' '--- intended paths ---'
test -f "$OBSIDIAN/20-areas/writing/blog/posts/pi-epub-translation-workflow.md"
find "$OBSIDIAN/20-areas/writing/blog/assets/pi-epub-translation-workflow" -maxdepth 1 -type f -name '*.png' -printf '%f\n' | sort
```

Expected: the pre-existing dirty paths remain present, and the only new paths are the one post plus five named assets.

- [ ] **Step 2: Re-run the translator extension smoke check from the isolated worktree**

```bash
cd /home/datlt/workspace/translator/.worktrees/pi-epub-translation-blog
pi --approve -e .pi/extensions/epub-translate/index.ts -p "Reply only: loaded"
```

Expected output: `loaded`.

- [ ] **Step 3: Leave the Obsidian draft uncommitted for review**

Do not run `git add`, `git commit`, `git push`, or change `status: draft`. Report the exact post path, asset directory, repository URL, and validation result so the user can review before a later publication decision.

---

## Final verification

Run the following after all tasks:

```bash
POST=/home/datlt/workspace/obsidian/20-areas/writing/blog/posts/pi-epub-translation-workflow.md
ASSETS=/home/datlt/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow
test "$(awk -F': ' '/^status:/{print $2; exit}' "$POST")" = draft
test "$(find "$ASSETS" -maxdepth 1 -type f -name '*.png' | wc -l)" -eq 5
! rg -ni 'computer-use|injaneity' "$POST"
printf '%s\n' 'Draft and five assets are present; publication remains disabled.'
```
