---
name: no-placeholder-check
description: Detect fake, placeholder, or non-functional generated app behavior. Use during Quality Review when checking dummy data, dead controls, TODO text, mock-only flows, and unsupported claims. Do not use for normal architecture or release writing.
---

# No Placeholder Check

## Purpose

Prevent generated demos from passing with fake UI, dead buttons, TODO text, or claims unsupported by working behavior.

## Boundaries

- Owns placeholder/fake behavior detection.
- Does not repair code directly.
- Escalates defects to Builder with exact evidence and expected real behavior.

## Inputs

- Generated app files, screenshots, QA findings, and requirements.
- Build summary and known intentional demo limitations.

## Workflow

1. Search visible UI and source for TODO, placeholder, lorem ipsum, fake auth, mock-only state, and disabled controls.
2. Compare UI claims against actual implemented behavior.
3. Check whether sample data is acceptable demo seed data or misleading fake functionality.
4. Mark each finding as pass, needs repair, or acceptable limitation.
5. Return exact repair requests with artifact refs.

## Output Contract

- QA report or tool result artifact.
- Placeholder findings, evidence, severity, and recommended next action.
- Dashboard-safe review comment.

## Quality Rules

- Seed data is acceptable only when the user can perform the promised flow.
- A button or form that does nothing is a defect.
- A limitation can pass only when it is explicit and does not contradict the requirements.

## Failure And Repair

- Retry after Builder repairs placeholder findings.
- Block when placeholder behavior is caused by missing external credentials.
- Human approval is required if the product intentionally accepts mock behavior.

## Examples

### Good invocation

Input: Generated app with "Save" button.
Output: Defect if Save does not persist or show meaningful result.

### Bad invocation

Input: UI with mock reports.
Output: Pass because the screen looks polished.
