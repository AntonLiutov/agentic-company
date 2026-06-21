---
name: git-pr-workflow
description: Deliver each work item as a real git branch + pull request, and (for QA) review and merge it. Use when the run has a connected repository and your work should land as a PR — covers branch/commit/push/PR for builders and review/merge/comment for QA. Do not use for runs with no repo host or for non-code work.
---

# Git & Pull Request Workflow

## Purpose

Turn each completed work item into a real pull request on the connected repository:
the builder pushes a per-item branch and opens (or updates) a PR; the QA reviewer
reviews that PR and merges it on a pass, or leaves a single general comment with the
defects on a fail. **The generated project directory IS the git working tree** — you
are already inside the repo, so you can run `git` and `gh` directly.

## When this applies

Only when the run has a connected repository. Your task context / execution request
tells you:

- `repository` (e.g. `owner/name`) and `base_branch` (e.g. `main`),
- your `work_item_id` and the branch convention `adl/<work-item-id>` (lowercase),
- whether a pull request already exists for this work item (its URL).

If there is **no repository** in your context, skip this skill entirely and just
deliver locally — do not run git/gh.

## Safety rules (always)

- **Never commit secrets**: `.env*`, `*.key`, `*.pem`, credentials, tokens. A
  `.gitignore` excluding them is seeded for you — keep it. Run `git status` and
  confirm no secret-looking file is staged before every commit; `git rm --cached` it
  if it is.
- **Never force-push**, and never modify, reset, rebase, or delete the base branch
  (`main`).
- **One branch per work item**: `adl/<work-item-id>`. Never reuse another item's
  branch or commit straight to the base branch.
- **Builders never merge.** Only QA merges, and only on a pass.
- Use `gh` for GitHub actions (auth is already configured). On Windows, if a `.ps1`
  shim is blocked by execution policy, call the `.cmd` shim (`gh.cmd` / `git`).

## Builder workflow (Fullstack / Deployment)

ALWAYS orient before you touch a branch — never blindly `git checkout -b`.

### 1. ORIENT — look before you act
```sh
git status                       # what did I just change (and is anything secret)?
git branch --show-current        # which branch am I on right now?
git log --oneline -8             # what commits already exist here?
git branch --list adl/<id>       # does THIS work item already have a branch?
gh pr list --head adl/<id>       # does THIS work item already have an open PR?
```

### 2. DECIDE — same feature or a new one?
- **Same work item** (a repair, or you're already on `adl/<id>`): EXTEND that branch —
  stay on it, commit your fix, push. The existing PR updates automatically; do not
  open a second one.
- **New work item** (you're on a different feature's branch or on the base branch):
  start a FRESH branch FROM THE UPDATED BASE, so it includes everything already
  merged — do not branch off another feature.

### 3. ACT
New work item — get your changes onto a fresh branch without losing them or prior work:
```sh
git stash --include-untracked                 # park your new changes
git fetch origin
git switch <base_branch> && git pull --ff-only # base now has prior merged items
git switch -c adl/<id>                         # new branch from the updated base
git stash pop                                  # apply your work (resolve conflicts: keep both)
```
Same work item (repair) — you're already on `adl/<id>`, just continue.

Then, in both cases:
```sh
git add -A && git status                       # re-check: unstage secrets (git rm --cached <f>)
git commit -m "feat(<id>): <title>"            # use fix(<id>): ... on a repair
git push -u origin adl/<id>
```
Pull request — open one only if none exists (you checked in step 1):
```sh
gh pr create --base <base_branch> --head adl/<id> --title "[<id>] <title>" --body "<what this delivers>"
```
If a PR already exists, your push already updated its commits — do NOT open a second one.
Also keep the PR details CURRENT so they always reflect the work now on the branch:
```sh
gh pr edit <pr> --title "[<id>] <title>" --body "<what this delivers now>"
```

Finally, report the branch and the PR URL in your execution summary.

## Reviewer workflow (QA)

**You are a PULL REQUEST reviewer who also runs functional tests — not a tester who
happens to have a repo.** The pull request IS the unit of work. The builder OPENS the
PR; YOU review it and decide its fate on a pass or a fail.

### HARD GATE — git comes FIRST, before anything else

Before you serve the app, before you open a browser, before any functional test, your
**FIRST action is to inspect git and the PR**. You are forbidden from running, serving,
or testing the app until you have completed Step 1 below and written its evidence.

Your QA report **MUST OPEN** with a `## Git state` block containing, in this order:

- `repository` and the branch you are on (`git remote -v`, `git branch --show-current`),
- the work item's PR number/URL (`gh pr list --head adl/<id>` if not given to you),
- a one-line summary of the diff you reviewed (`gh pr diff <pr>`),
- confirmation that the pushed branch matches the code you will test.

**A QA report that does not OPEN with a filled-in `## Git state` block is INVALID and
counts as a FAIL of your own process — redo it.** This is non-negotiable.

### 1. ORIENT — inspect git/the PR FIRST (mandatory, before any app exercise)
```sh
git remote -v                    # which repository is this?
git status                       # is the tree clean? anything uncommitted?
git branch --show-current        # which branch am I on right now?
git log --oneline -5             # what commits are here?
gh pr list --head adl/<id>       # find THIS work item's PR if its URL wasn't given to you
gh pr diff <pr>                  # what does the PR actually change?
```
Then prove the PR is real and current before you trust it:
- `gh pr diff <pr>` must reflect what is actually on branch `adl/<id>`.
- The branch you tested must be the branch the PR points at.
- If local has commits that were never pushed, or the pushed branch does NOT match the
  code you tested, that is a **FAIL** — never review or merge stale or unpushed code.

Only after Step 1 is done and its `## Git state` block is written may you proceed.

### 2. REVIEW
Serve and exercise the app (browser + functional QA) AND re-read the PR diff together,
checking the running app against exactly what the PR changes.

### 3. DECIDE — you own the merge (the platform does NOT touch git)
- **Pass** → MERGE the PR yourself: `gh pr merge <pr> --squash --delete-branch` (you have
  full git access). You MAY also leave ONE short positive comment with your verdict:
  `gh pr comment <pr> --body "<one-line verdict>"` (one comment, not a thread). Report
  `passed`.
- **Fail** → leave ONE general comment with concrete defects and reproduction steps:
  `gh pr comment <pr> --body "<findings>"`. NEVER merge a failed PR. Report `failed` so the
  builder repairs the same branch and you re-review.
- **Environment blocker** (outbound-network/sandbox/credentials, not an app defect)
  → do not file it as a defect; report `blocked` for human attention.

## Result vocabulary

- Builder: report the branch + PR URL, and whether it was a new PR or an update.
- QA: `passed` (reviewed and merged), `failed` (commented, repair needed), `blocked`
  (environment limitation, not an app defect).

## Examples

### Good (builder)

Input: F2 implemented; repo connected; base `main`; no PR yet.
Output: pushed `adl/f2`, opened PR "[F2] …", reported the URL in the summary.

### Good (QA)

Input: PR for F1 exists; the app works in the browser.
Output: opened with a `## Git state` block, reviewed the PR diff, merged it yourself
(`gh pr merge`), left a one-line positive comment, reported `passed`.

### Bad

Input: repo connected.
Output: committed straight to `main`, OR opened a second PR for the same item, OR
merged a failing PR. Never do any of these.
