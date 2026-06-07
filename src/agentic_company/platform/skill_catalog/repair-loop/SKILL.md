---
name: repair-loop
description: Route failed or blocked work through bounded recovery. Use for Head or Team Lead coordination when QA, deployment, status inspection, or specialist execution finds repairable defects. Do not use for normal first-pass implementation without a failure or repair signal.
---

# Repair Loop

## Purpose

Turn failures into bounded, traceable repair work instead of silent retries or final run failure.

## Boundaries

- Owns repair routing, retry budget, blocker classification, and next-action clarity.
- Does not directly implement product code unless routed through the responsible specialist.
- Escalates to human approval when repair requires secrets, risky scope change, or exhausted budget.

## Inputs

- Current delivery state, blocker list, QA findings, deployment failures, status inspection results, and artifact refs.
- Work item ID, feature ID, sprint ID, and external board reference when available.

## Workflow

1. Classify the problem as repairable, blocked, needs human approval, or terminal failure.
2. Identify the responsible agent and exact artifact evidence.
3. Create a concrete repair message with reproduction steps and expected outcome.
4. Respect retry budget and avoid looping on the same vague error.
5. Route repair to Builder, QA, Deployment, or Release Reporter as appropriate.
6. Return dashboard-safe status, blocker details, and recommended next action.

## Output Contract

- Tool result or execution summary artifact.
- Repair status, responsible next agent, artifact refs, retry count, and blocker reason.
- Dashboard-safe comment suitable for board and issue updates.

## Quality Rules

- Never retry without new evidence or a specific repair instruction.
- Do not hide provider limits, missing secrets, or environment failures as product defects.
- Preserve traceability from finding to repair attempt to final status.

## Failure And Repair

- Retry only while repair budget remains and the issue is concrete.
- Block when repair requires human/environment action.
- Human approval is required for high-risk actions or autonomous merge/deploy decisions.

## Examples

### Good invocation

Input: QA reports that Save button does not persist.
Output: Builder repair request citing artifact IDs and expected persistence behavior.

### Bad invocation

Input: Deployment failed because secret is missing.
Output: Retry deployment repeatedly without asking for the secret.
