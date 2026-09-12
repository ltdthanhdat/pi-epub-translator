# Design: Draft Blog for the Pi EPUB Translation Workflow

- Date: 2026-09-12
- Status: proposed
- Target repositories: `translator`, `obsidian`

## Decision

Create an English draft article titled **I Created a Pi Workflow to Translate EPUB Books Without Losing Their Structure**. The article documents the existing Pi EPUB translation workflow and links to the renamed private GitHub repository:

`https://github.com/ltdthanhdat/pi-epub-translator`

The `@injaneity/pi-computer-use` package is an internal capture tool only. It will not be named, linked, or presented as part of the article's subject.

## Goals

- Rename the existing private GitHub repository `ltdthanhdat/epub-translator` to `ltdthanhdat/pi-epub-translator`.
- Push the current tracked `master` history to the renamed repository.
- Capture honest screenshots of the real Pi TUI workflow using the local EPUB sample.
- Create a reviewable blog draft in the canonical Obsidian blog source.
- Keep the draft compatible with the existing Obsidian blog CI and later publication flow.

## Non-goals

- Do not create a second GitHub repository.
- Do not publish the article yet; keep `status: draft`.
- Do not commit or push `input/`, EPUB source files, generated output, run state, or other ignored artifacts.
- Do not modify the Obsidian workflow files or unrelated dirty files.
- Do not make `pi-computer-use` part of the product story or article dependencies.

## Repository operation

Use `gh` to rename the existing repository, then add/update the local `origin` remote and push the current committed `master` branch. The local `.gitignore` modification is user-owned and must remain unstaged. Before pushing, verify that only committed project files are included; the ignored EPUB sample remains local.

The expected public URL, even while the repository is private, is:

`https://github.com/ltdthanhdat/pi-epub-translator`

If the remote contains history that cannot be fast-forwarded from the local repository, stop and report the divergence instead of force-pushing.

## Screenshot capture

Install `@injaneity/pi-computer-use` through Pi only for the capture session. Use it to observe/control the local terminal/TUI and save screenshots outside the translator repository until they are selected for the article.

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
- Later publication is a separate action: change `status` to `published`, review the resulting diff, merge to `master`, and let `publish-blog.yml` reconcile the projection.

## Acceptance criteria

- Existing GitHub repository is renamed, not duplicated, and current `master` is pushed without force.
- Translator repository does not contain the EPUB sample or generated run artifacts.
- Five truthful screenshots are stored under the article asset directory.
- Blog draft has valid required frontmatter, the exact approved title, the private repository link, and no computer-use package mention.
- Obsidian's unrelated working-tree changes remain untouched.
- Blog validation passes, or any environment limitation is reported explicitly.
