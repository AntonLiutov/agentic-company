---
name: screenshot-review
description: Review screenshots for visible UI quality issues. Use during Quality Review for layout breakage, overflow, cropped content, contrast problems, unreadable text, and responsive regressions. Do not use for implementation or deployment setup.
---

# Screenshot Review

## Purpose

Catch visual defects that source-code checks miss, especially broken layouts after generation or deployment.

## Boundaries

- Owns screenshot-based visual review and defect description.
- Does not repair UI directly.
- Escalates findings to Builder with exact screen/viewport evidence.

## Inputs

- Screenshot evidence artifacts.
- Requirements, design expectations, QA report, and deployed/local URLs.
- Viewport or device context when available.

## Workflow

1. Inspect screenshots for overlap, clipping, unreadable text, horizontal overflow, broken spacing, and missing content.
2. Compare the screen against the expected product flow.
3. Identify whether the defect blocks the demo or is minor polish.
4. Produce concrete repair requests with viewport and location notes.
5. Return screenshot evidence and review status.

## Output Contract

- Screenshot evidence and QA report references.
- Visual findings with severity and recommended next action.
- Dashboard-safe comment for Quality Review.

## Quality Rules

- Do not pass if primary controls are hidden, clipped, or unreadable.
- Do not over-polish minor issues that do not affect demo usability.
- Include viewport/device context whenever possible.

## Failure And Repair

- Retry after Builder provides new screenshot evidence.
- Block when no screenshot or visual inspection path is available for a UI-heavy task.
- Human approval is required for subjective visual tradeoffs in high-stakes demos.

## Examples

### Good invocation

Input: Mobile screenshot with cropped board column.
Output: Repair request naming viewport, location, and expected fit.

### Bad invocation

Input: Screenshot with overlapped text.
Output: Pass because tests succeeded.
