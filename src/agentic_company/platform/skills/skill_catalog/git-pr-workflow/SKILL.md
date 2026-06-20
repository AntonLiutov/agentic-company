---
name: git-pr-workflow
description: Review connected-repository work item context without assuming worker-side GitHub credentials. Builders produce file changes only; the platform commits, pushes, opens PRs, and merges after QA pass. Use when a run has a connected repository and PR evidence matters. Do not use for runs with no repo host.
---

# Git & Pull Request Workflow

## Purpose

Turn each completed work item into a real pull request on the connected repository,
with GitHub write operations owned by the platform. Builders and deployment workers
produce file changes and evidence only. After the worker finishes, Agentic Delivery
Lab commits, pushes, opens or updates the PR, records it, and mirrors it to the board.
QA reviews the recorded PR and returns a verdict; the platform merges on a pass.

## When This Applies

Only when the run has a connected repository. Your task context / execution request
tells you:

- `repository` and `base_branch`;
- your `work_item_id` and the platform branch convention `adl/<work-item-id>`;
- whether a pull request already exists for this work item.

If there is no repository in your context, skip this skill and deliver locally.

## Safety Rules

- Never commit secrets: `.env*`, `*.key`, `*.pem`, credentials, tokens, `.npmrc`,
  `.pypirc`, or generated auth files.
- Never force-push, reset, rebase, delete, or commit directly to the base branch.
- Workers do not own GitHub credentials. Do not run `gh`, push, open PRs, merge PRs,
  or assume host GitHub auth is available inside the sandbox.
- QA returns a verdict only. The platform merges host-side after a passing verdict.

## Builder Workflow

Do not branch, commit, push, or open a PR from the worker. Implement the assigned
work item in the generated project, avoid secrets, run the relevant local checks,
and write a concise summary. The platform will publish the diff to `adl/<id>` and
open or update the PR after your execution succeeds.

Your summary should include:

- the work item id and scope delivered;
- key files changed;
- local verification commands and results;
- any limitations or follow-up needed.

## Reviewer Workflow

You are a pull request reviewer who also runs functional tests. The platform opens
or updates the PR. You review it and return a verdict; you do not merge.

Before runtime/browser validation, inspect the repository and recorded PR context
provided in the task. When a PR URL is provided, review its diff and confirm the
code you test matches the PR branch. If no PR is recorded for a code-producing work
item, report the missing PR as a platform/workflow blocker only when the work item
contract requires a PR.

Decision rules:

- Pass: report `passed`. The platform merges after your verdict.
- Fail: report `failed` with concrete defects and reproduction steps.
- Environment blocker: report `blocked` when credentials, sandbox, outbound network,
  or runtime availability prevents evidence collection.

## Result Vocabulary

- Builder: `completed` with changed files and verification evidence.
- QA: `passed`, `failed`, or `blocked`.
- Platform: records PR URL, comments, and merge state outside the worker sandbox.
