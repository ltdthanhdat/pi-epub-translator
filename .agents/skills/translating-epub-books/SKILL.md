---
name: translating-epub-books
description: Use when translating an epub book's chapter files into another language while keeping the original HTML structure, styling, images, footnotes, and epub packaging intact
---

# Translating EPUB Books

## Overview

An epub is a zip of XHTML files + CSS + images (see `content.opf` for reading order). Translating "in place" means swapping only the human-readable text inside each XHTML file — every tag, id, href, and attribute stays exactly as-is unless explicitly noted below. Getting this wrong silently breaks internal footnote links, the table of contents, and epub validity.

## When to Use

- Translating a whole book (or chapters) from an existing epub, output should be a working epub in the target language.
- NOT for translating plain prose pasted by the user (no HTML to preserve) — just translate normally.

## Workflow

1. **List models before asking the user to choose.** After the user chooses a backend, open that logged-in CLI's native `/model` picker in a short interactive session. Copy the picker rows that are enabled for this account into chat, then wait for the user to choose one. Never use a hard-coded model list, infer availability from public documentation, choose a model, or answer the coordinator's menu on the user's behalf. If the picker cannot be read, report that and ask the user for an exact model name instead.
2. **Ask the remaining choices in chat.** Ask for reasoning effort (for Codex), target language, its BCP-47 code (for example Vietnamese / `vi`), worker count, and whether to translate Index or Notes. Wait for all choices before starting any translation.
3. **Put source books in `input/`.** The coordinator writes only to `output/` and never overwrites a source EPUB.
4. **Create a glossary file before starting**, then run the coordinator with the exact choices: `python3 .agents/skills/translating-epub-books/scripts/parallel_translate.py --root "$PWD" --book "BOOK.epub" --backend codex --model "USER_SELECTED_MODEL" --reasoning-effort xhigh --target-language Vietnamese --target-language-code vi --workers 4 --glossary glossary.txt`. The coordinator snapshots the glossary into the run checkpoint, uses SQLite leases and separate result files, and resumes without re-translating completed fragments. It intentionally does not use tmux; tmux/Herdr may observe the command but is not its state store.
5. **Extract manually only for a one-off chapter** (`scripts/epub_tool.py extract book.epub out/`). Read `OEBPS/content.opf` for the manifest/spine — that's the file list and reading order.
6. **Build a glossary first**, before translating any chapter. Scan a few chapters for recurring proper nouns (people, dynasties, places, institutions) and fix their target-language rendering once — e.g. `Cochinchina → Nam Kỳ`, `Annam → Trung Kỳ`, `Tonkin → Bắc Kỳ`, `Nguyen dynasty → nhà Nguyễn`. Reuse this glossary for every chapter so the same entity isn't rendered three different ways across the book.
7. **Decide what NOT to translate**: by default the coordinator skips `Index.xhtml` and `Notes.xhtml` but preserves both byte-for-byte in the output. It translates the EPUB navigation document (`nav.xhtml`) even when it is outside the spine. Enable `--translate-index` or `--translate-notes` only when the user requests them.
8. **Translate each chapter file**, following the preserve/translate rules below.
9. **Repackage and verify.** The coordinator validates the ZIP layout, intended members, XHTML structure, BCP-47 language attributes, and that temporary pagebreak markers do not leak. Also run `epubcheck` when installed, or `unzip -t` otherwise.

## Preserve vs. Translate

| Element | Rule |
|---|---|
| Tags, `class`, `id`, `epub:type`, `href` | Never touch. Footnote links (`<a href="Notes.xhtml#ci_rfn1">`), page-break markers (`<span epub:type="pagebreak" id="page_5" title="5"/>`), TOC anchors all depend on these staying byte-identical. |
| `<html lang="en" xml:lang="en">` | This one DOES change — update to the target language code (e.g. `vi`). |
| Visible text inside tags | Translate. This is the whole point. |
| URLs shown as link text (`<a href="http://x.com">www.x.com</a>`) | Never translate/alter the visible URL. |
| Book/film titles in italics (`<i>Fire in the Lake</i>`) | Keep the original title; optionally add the translated title in parentheses on first mention only — don't invent a new canonical title. |
| Foreign/loan words already italicized in source (`<i>kinh</i>`, `<i>mission civilisatrice</i>`) | Translate the surrounding sentence, but check whether the italicized word already IS a target-language word (common when translating a book about a country into that country's own language) — don't re-translate it into itself. |
| **Split decorative capitals** — a normal-size first letter followed by `<small>REST</small>`, used two ways: (a) chapter-opening dropcap where the small-caps run can span the rest of the first word plus the next word or two (e.g. `<span class="dropcap">M</span><small>OST AMERICAN READERS</small>` = "MOST AMERICAN READERS"), or (b) a heading where EACH word gets its own initial-letter/small-caps split (e.g. `M<small>ULTIPLE</small> V<small>IETNAMS</small>` = "MULTIPLE VIETNAMS") | Translate the sentence/heading first, THEN re-derive the split from the *translated* wording: pull out the first letter(s) of the new word(s) into the plain span, put the rest in `<small>`. The split point moves because the first word(s) changed language — e.g. "Phần lớn độc giả Mỹ" splits as `<span class="dropcap">P</span><small>HẦN LỚN ĐỘC GIẢ MỸ</small>`. This is the single easiest thing to get wrong — a naive pass leaves the old English letters untranslated inside the spans. |
| Section headings built the same way (`<p class="h3">M<small>ULTIPLE</small> V<small>IETNAMS</small></p>` = "MULTIPLE VIETNAMS") | Same rule: translate the heading text, then re-split each word's first letter out. |
| Curly quotes `‘ ’ “ ”`, em dashes | Keep the same punctuation style unless the target language has its own convention (Vietnamese typically uses `"..."` — pick one convention and apply it consistently, don't mix). |

## Common Mistakes

- Translating the letters trapped inside `dropcap`/`small` spans literally, instead of re-deriving the split from the newly-translated word.
- Renaming the same historical figure/place differently in chapter 3 vs chapter 9 — always check the glossary first.
- Translating or renumbering footnote ids/hrefs — this silently disconnects every footnote link in the book.
- Fully translating the Index — it's page-number-keyed and stops making sense once pagination changes; ask the user first.
- Missing that `<html lang="en">` needs updating even though other attributes must not change.

## Mechanical Steps

Extraction/repackaging is scriptable, not a judgment call — use `scripts/epub_tool.py` (`extract` / `build` subcommands) rather than hand-rolling zip logic each time. It preserves the required uncompressed-first `mimetype` entry.

## Parallel coordinator

`scripts/parallel_translate.py` splits the EPUB into complete XHTML text blocks, not print-page markers: pagebreak markers occur inside paragraphs. It skips image-only blocks because attributes are never translated. Empty pagebreak spans are represented by a literal temporary marker in the worker prompt, restored before structural validation, then removed from the output.

Each Codex/Claude process writes only its own result; the coordinator validates matching XHTML structure, retries a failed fragment up to `--max-attempts` (default 3), then records recovery state in `.parallel-translate/<book>/run.sqlite`. On restart, completed results are retained. Pass `--workers N` to safely change the pool size; it updates only the stored worker count. A run's glossary and translation scope are fixed once jobs exist; start a new run to change them.
