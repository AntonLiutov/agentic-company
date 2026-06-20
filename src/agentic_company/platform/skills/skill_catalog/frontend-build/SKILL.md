---
name: frontend-build
description: Build or repair generated application UI and runtime behavior from an assigned ADL work item. Use for Fullstack implementation, UI repair, broken buttons, layout defects, and app runtime defects. Do not use for QA-only validation, deployment-only work, or release reporting.
---

# Frontend Build

## Purpose

Implement the assigned product slice as real working software while preserving the generated project's architecture and producing evidence for QA.

## Boundaries

- Owns application code, UI behavior, data flow, local run fixes, and implementation summary.
- Does not mark work as quality-approved or deployed.
- Escalates when requirements are impossible, secrets are missing, or environment setup blocks local validation.

## Inputs

- Assigned work item or repair request.
- Requirements, architecture, delivery plan, and prior QA artifacts.
- Existing generated project files and current runtime/deployment context.

## Workflow

1. Read the assigned work packet and referenced artifact IDs.
2. Inspect the existing app before editing.
3. Implement the smallest vertical slice that satisfies the work item.
4. Build real behavior; do not leave fake data, placeholder UI, or dead buttons unless explicitly requested.
5. Respect existing styling, routing, persistence, and responsive constraints.
6. Run available local checks or explain why they cannot run.
7. Return changed files, evidence, blockers, and artifact refs.

## Output Contract

- Status and implementation summary.
- Execution summary artifact and optional screenshot or tool-result evidence.
- Dashboard-safe comment describing what changed and what QA should verify.

## Quality Rules

- Prefer small, coherent changes over broad rewrites.
- Keep text within containers and avoid broken responsive layouts.
- Preserve user-requested behavior exactly.
- Report uncertainty instead of silently inventing hidden features.

## Failure And Repair

- Retry when QA findings are concrete and repairable.
- Block when dependency, secret, or environment action is required.
- Human approval is required before destructive changes or risky scope expansion.

## Examples

### Good invocation

Input: Work item for random joke and number buttons.
Output: Working app changes, summary, local evidence, and artifact refs.

### Bad invocation

Input: Repair request for broken styling.
Output: A rewrite that changes the product scope and leaves untested buttons.
