# Design: Clean Pi EPUB Translator Repository and Draft Blog

- Date: 2026-09-12
- Status: approved
- Target repositories: `translator`, `obsidian`

## Decision

Prepare the existing private GitHub repository as a clean, reviewable Pi extension repository. Move the general EPUB translation skill to the user-level location `$HOME/.agents/skills/translating-epub-books`, remove that skill and unrelated planning history from the repository snapshot, add a README with installation and usage instructions, and publish one clean initial commit to the renamed private repository:

`https://github.com/ltdthanhdat/pi-epub-translator`

The existing commit history will be replaced by the clean snapshot after creating a local recovery bundle. The remote remains private for review.

Create an English draft article titled **I Created a Pi Workflow to Translate EPUB Books Without Losing Their Structure**. The article documents the Pi extension and links to the private repository. The `@injaneity/pi-computer-use` package is an internal capture tool only; it will not be named, linked, or presented as part of the article's subject.

## Goals

- Move the reusable skill to `$HOME/.agents/skills/translating-epub-books` and update its internal commands to use the new absolute user-level location.
- Keep the repository product-focused: the Pi extension, its tests/package metadata, `.gitignore`, and `README.md` only.
- Add installation, runtime, command, sample, and testing instructions to `README.md`.
- Replace the old local and remote repository history with one clean initial commit, without force-pushing before a recovery bundle and remote SHA check exist.
- Rename the existing private GitHub repository from `ltdthanhdat/epub-translator` to `ltdthanhdat/pi-epub-translator` and keep it private.
- Capture honest screenshots of the real Pi TUI workflow using the local EPUB sample.
- Create a reviewable blog draft in the canonical Obsidian blog source.
- Keep the draft compatible with the existing Obsidian blog CI and later publication flow.

## Non-goals

- Do not create a second GitHub repository.
- Do not publish the article yet; keep `status: draft`.
- Do not push `input/`, EPUB source files, translated output, run state, or other ignored artifacts.
- Do not mention or link to `pi-computer-use` in the article or README.
- Do not modify the Obsidian workflow files or unrelated dirty files.
- Do not alter `30-resources/knowledge/raw/`.
- Do not preserve the old commit history on the public branch after the user-approved cleanup; retain only a local recovery bundle during the operation.

## Repository contents after cleanup

The clean repository snapshot contains:

```text
.pi/extensions/epub-translate/
.gitignore
README.md
```

The `.pi/extensions/epub-translate/` directory includes the existing extension source, worker helpers, package metadata, and tests. The old `.agents/skills/translating-epub-books/` directory and `docs/` planning/spec files are not part of the clean repository snapshot.

## Skill migration

Copy the skill to `$HOME/.agents/skills/translating-epub-books`, update the skill's command examples from the repository-relative `.agents/skills/translating-epub-books/...` path to `$HOME/.agents/skills/translating-epub-books/...`, run its Python tests from the new location, and then remove the repository copy. The Pi extension is independent of that skill and must continue to pass its own TypeScript/Python tests after the removal.

## README contents

`README.md` explains:

- what the Pi extension does;
- required Pi, Node.js, Python, and model/API setup;
- how to install extension dependencies;
- how to launch Pi with `pi --approve -e .pi/extensions/epub-translate/index.ts`;
- the `/translate-epub`, `/epub-progress`, `/epub-cancel`, and retry workflow;
- where to put the local EPUB sample and why input/output/run directories stay ignored;
- how to run the extension tests;
- the private GitHub repository URL.

The README describes the extension itself, not the internal screenshot utility or the removed general-purpose skill.

## Repository cleanup and history rewrite

Before destructive Git operations:

1. Verify the existing remote owner/name and current remote `main` SHA.
2. Create a local recovery bundle containing the current local refs.
3. Build a clean snapshot containing only the approved repository contents.
4. Create one root commit with a clear message such as `Initial import: Pi EPUB translator`.
5. Rename the existing GitHub repository in place.
6. Verify that the renamed remote is still private and that no unexpected remote movement occurred.
7. Force-replace only remote `main` with the clean root commit using an explicit expected-old-SHA lease; never use an unguarded `--force`.
8. Verify the remote `main` tree, default branch, visibility, and commit count.

The old history is not kept as a visible remote branch. The recovery bundle remains local until the user confirms the cleanup is no longer needed.

## Screenshot capture

Install `@injaneity/pi-computer-use` through Pi only for the capture session. Use it to observe/control the local terminal/TUI and save screenshots outside the repositories until they are selected for the article.

Capture these states from the actual workflow:

1. EPUB selection in `/translate-epub`.
2. Model, thinking/effort, and worker-count choices.
3. Blocking AI inspection/loading state.
4. Scope and glossary review before locking the run.
5. Translation progress and, if a real configured model run completes, the verified output state.

Screenshots should show the workflow rather than reproduce book content. Avoid exposing long copyrighted passages, credentials, session identifiers, or local filesystem secrets. Do not fabricate a completion image if a real end-to-end run cannot complete; use the latest truthful state and note the limitation.

## Blog source

Create one file:

`~/workspace/obsidian/20-areas/writing/blog/posts/pi-epub-translation-workflow.md`

Use the required frontmatter:

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

Store selected images under:

`~/workspace/obsidian/20-areas/writing/blog/assets/pi-epub-translation-workflow/`

Use the existing Obsidian image-link style and descriptive captions. The article may link to the private GitHub repository. It must not mention or link to the computer-use package.

## Article structure

1. **The problem: translating an EPUB is not just translating text** — explain XHTML documents, navigation, metadata, footnotes, styling, consistency, and retryability.
2. **The project** — introduce the Pi extension and the `pi-epub-translator` repository.
3. **The translation workflow** — show EPUB selection; model/effort/workers; AI document-scope classification; BCP-47 inference; glossary review; lock/start; progress and output.
4. **What happens under the hood** — describe fragment preparation, stateless Pi workers, SQLite leases/fencing, retries, merge, and verification.
5. **Why human review stays in the loop** — explain scope editing, glossary locking, model/effort/worker choices, cancel, and retry.
6. **What worked and what is still limited** — distinguish structural preservation from literary editing, mention model dependence and current EPUB validation limits, and avoid claiming unsupported production readiness.
7. **Conclusion** — summarize the orchestration lesson and link the repository.

The tone should be a first-person engineering build note, not a package advertisement. Target length is approximately 1,200–1,800 words, with five screenshots integrated near the relevant workflow sections.

## Validation and safety

- Preserve all existing uncommitted changes in `obsidian`, including dashboard files, deleted issue artifacts, `.trash/`, and workspace configuration.
- Do not touch `30-resources/knowledge/raw/`.
- Validate Markdown/frontmatter and the blog projection using the existing CI flow where practical. The CI source of truth remains `20-areas/writing/blog`; no generated blog repository files are edited manually.
- Because the article remains a draft, verify that it is structurally valid without triggering publication.
- Run the extension tests and migrated skill tests after removing the repository skill copy.
- Later publication is a separate action: change `status` to `published`, review the resulting diff, merge to `master`, and let `publish-blog.yml` reconcile the projection.

## Acceptance criteria

- `$HOME/.agents/skills/translating-epub-books` exists and its tests pass.
- The repository contains only the Pi extension tree, `.gitignore`, and `README.md` in the clean remote snapshot.
- The old `.agents` skill and `docs/` planning files are absent from the clean remote snapshot.
- The existing GitHub repository is renamed, remains private, and remote `main` contains exactly one clean root commit created without an unguarded force push.
- The translator repository does not contain the EPUB sample or generated run artifacts.
- Five truthful screenshots are stored under the article asset directory.
- The blog draft has valid required frontmatter, the exact approved title, the private repository link, and no computer-use package mention.
- Obsidian's unrelated working-tree changes remain untouched.
- Blog validation passes, or any environment limitation is reported explicitly.
