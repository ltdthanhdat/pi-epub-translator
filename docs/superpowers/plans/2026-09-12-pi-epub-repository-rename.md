# Pi EPUB Translator Repository Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the existing private GitHub repository to `ltdthanhdat/pi-epub-translator` and push the current tracked translator history without force-pushing or including ignored EPUB artifacts.

**Architecture:** Operate on the existing GitHub repository in place with `gh repo rename`, then attach the local repository to the renamed SSH remote and push the local `master` branch. The local checkout has an intentional uncommitted `.gitignore` change; it remains unstaged and is not part of the push.

**Tech Stack:** Git, GitHub CLI (`gh`), GitHub SSH remote.

**Spec:** `docs/superpowers/specs/2026-09-12-pi-epub-translation-blog-design.md`

## Global Constraints

- Rename the existing repository; do not create a second repository.
- Do not force-push.
- Do not stage or commit the local `.gitignore` change.
- Do not push ignored `input/`, `output/`, `.parallel-translate/`, or worktree files.
- Stop and report if the remote history is not compatible with the local `master` history.

---

### Task 1: Verify local and remote history before mutation

**Files:**
- Read only: `/home/datlt/workspace/translator/.gitignore`
- Read only: Git refs and GitHub repository metadata

**Interfaces:**
- Consumes: local `/home/datlt/workspace/translator` checkout and `ltdthanhdat/epub-translator`.
- Produces: a recorded preflight showing local branch, dirty files, remote refs, and whether a non-force push is safe.

- [ ] **Step 1: Record local status without staging anything**

```bash
cd /home/datlt/workspace/translator
git status --short --branch
git log --oneline -5
git ls-files
```

Expected: branch `master`; only the known `.gitignore` modification is dirty; EPUB files do not appear in `git ls-files`.

- [ ] **Step 2: Inspect the existing remote repository**

```bash
gh repo view ltdthanhdat/epub-translator --json name,visibility,defaultBranchRef,url
git ls-remote --heads https://github.com/ltdthanhdat/epub-translator.git
```

Expected: the existing private repository is found and its refs are recorded. Do not rename it if the command fails or resolves to an unexpected owner/repository.

- [ ] **Step 3: Compare the local commit with any existing remote branch**

```bash
cd /home/datlt/workspace/translator
LOCAL=$(git rev-parse master)
REMOTE_MAIN=$(git ls-remote https://github.com/ltdthanhdat/epub-translator.git refs/heads/master | awk '{print $1}')
printf 'local=%s\nremote-master=%s\n' "$LOCAL" "$REMOTE_MAIN"
if [ -n "$REMOTE_MAIN" ] && ! git merge-base --is-ancestor "$REMOTE_MAIN" master; then
  echo 'Remote master is not an ancestor of local master; stop before rename/push.'
  exit 1
fi
```

Expected: either no remote `master` exists, or it is an ancestor of local `master`. If this check fails, leave the repository unchanged and report the divergence.

### Task 2: Rename the existing repository and push `master`

**Files:**
- Modify remote repository name only: `ltdthanhdat/epub-translator` → `ltdthanhdat/pi-epub-translator`
- Modify local Git remote configuration only

**Interfaces:**
- Consumes: the successful preflight from Task 1.
- Produces: `https://github.com/ltdthanhdat/pi-epub-translator` containing the local `master` history.

- [ ] **Step 1: Rename the existing GitHub repository in place**

```bash
gh repo rename pi-epub-translator --repo ltdthanhdat/epub-translator --yes
```

Expected: GitHub reports the repository was renamed; no new repository is created.

- [ ] **Step 2: Configure the renamed SSH remote**

```bash
cd /home/datlt/workspace/translator
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin git@github.com:ltdthanhdat/pi-epub-translator.git
else
  git remote add origin git@github.com:ltdthanhdat/pi-epub-translator.git
fi
git remote -v
```

Expected: fetch and push URLs both point to `ltdthanhdat/pi-epub-translator.git`.

- [ ] **Step 3: Push without force**

```bash
cd /home/datlt/workspace/translator
git push -u origin master
```

Expected: exit code 0 and a normal `master` push. If Git rejects the push, do not add `--force`; stop and report the remote state.

- [ ] **Step 4: Set the renamed repository's default branch only if needed**

```bash
gh repo edit ltdthanhdat/pi-epub-translator --default-branch master
```

Run this only when the preflight showed a different default branch and the pushed `master` is the intended branch.

### Task 3: Verify the rename and preserve local state

**Files:**
- Read only: `/home/datlt/workspace/translator`

**Interfaces:**
- Consumes: the renamed repository and local remote configuration.
- Produces: verified repository URL and a cleanly scoped local status.

- [ ] **Step 1: Verify GitHub metadata and pushed ref**

```bash
gh repo view ltdthanhdat/pi-epub-translator --json name,visibility,defaultBranchRef,url
git ls-remote --heads origin master
```

Expected: name `pi-epub-translator`, visibility `PRIVATE`, default branch `master`, and the remote `master` SHA equals `git rev-parse master`.

- [ ] **Step 2: Confirm ignored artifacts were not tracked**

```bash
cd /home/datlt/workspace/translator
if git ls-tree -r --name-only HEAD | rg -q '^(input|output|\.parallel-translate)/'; then
  echo 'Ignored EPUB or generated artifacts are tracked; stop.'
  exit 1
fi
git status --short --branch
```

Expected: no ignored EPUB or generated artifact paths in the tree; the pre-existing `.gitignore` modification remains the only local dirty change.

- [ ] **Step 3: Record the final link for the blog draft**

```text
https://github.com/ltdthanhdat/pi-epub-translator
```

Expected: this exact private URL is used in the draft article.

---

## Final verification

Run the following after all tasks:

```bash
cd /home/datlt/workspace/translator
test "$(git ls-remote --heads origin master | awk '{print $1}')" = "$(git rev-parse master)"
git status --short --branch
```

The final output must show the remote SHA match and must not show any newly staged or committed `.gitignore` change.
