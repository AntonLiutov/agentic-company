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
If a PR already exists, the push already updated it — do NOT open a second one.

Finally, report the branch and the PR URL in your execution summary.

## Reviewer workflow (QA)

You review an existing PR and decide its fate. Your responsibility (different from the
builder's): the builder OPENS the PR; YOU review it and you are the one who MERGES it on a
pass or comments on a fail. ALWAYS orient before you judge — same discipline as the builder.

### 1. ORIENT — confirm the repo, the branch, and that you test what's pushed
```sh
git remote -v                    # which repository is this?
git status                       # is the tree clean? anything uncommitted?
git branch --show-current        # which branch am I on right now?
git log --oneline -5             # what commits are here?
gh pr list --head adl/<id>       # find THIS work item's PR if its URL wasn't given to you
```
Then prove the PR is real and current before you trust it:
- `gh pr diff <pr>` must reflect what is actually on branch `adl/<id>`.
- The branch you tested must be the branch the PR points at.
- If local has commits that were never pushed, or the pushed branch does NOT match the code
  you tested, that is a **FAIL** — never review or merge stale or unpushed code.

### 2. REVIEW
Serve and exercise the app (browser + functional QA) AND read the PR diff together.

### 3. DECIDE — this call is yours and the merge is MANDATORY on a pass
- **Pass** → you MUST merge it (required, not optional):
  `gh pr merge <pr> --squash --delete-branch`. Report `passed` and that you merged.
- **Fail** → leave ONE general comment with concrete defects and reproduction steps:
  `gh pr comment <pr> --body "<findings>"`. Do NOT merge, and do NOT open resolvable review
  threads. Report `failed` so the builder repairs the same branch and you re-review.
- **Environment blocker** (outbound-network/sandbox/credentials, not an app defect)
  → do not merge and do not file it as a defect; report `blocked` for human attention.

## Result vocabulary

- Builder: report the branch + PR URL, and whether it was a new PR or an update.
- QA: `passed` (reviewed + merged), `failed` (commented, repair needed), `blocked`
  (environment limitation, not an app defect).

## Examples

### Good (builder)

Input: F2 implemented; repo connected; base `main`; no PR yet.
Output: pushed `adl/f2`, opened PR "[F2] …", reported the URL in the summary.

### Good (QA)

Input: PR for F1 exists; the app works in the browser.
Output: `gh pr merge <pr> --squash --delete-branch`; reported `passed` + merged.

### Bad

Input: repo connected.
Output: committed straight to `main`, OR opened a second PR for the same item, OR
merged a failing PR. Never do any of these.
