---
name: browser-smoke-qa
description: Validate generated app behavior in a browser or equivalent runtime. Use for Quality Reviewer work, main user flows, buttons, navigation, forms, persistence, and visible errors. Do not use for implementation, deployment setup, or release writing.
---

# Browser Smoke QA

## Purpose

Verify that the generated app behaves like a usable prototype, not only that files exist.

## Boundaries

- Owns browser/runtime behavior validation and pass/fail evidence.
- Does not repair code directly unless explicitly routed through Builder.
- Escalates clear defects as repair requests with exact reproduction steps.

## Inputs

- Implemented feature or deployed/local URL.
- Requirements, delivery plan, prior build summary, and screenshot artifacts.
- Known environment constraints and test commands.

## Workflow

1. Open the app or use the closest available runtime check.
2. Exercise the primary user flow from the requirements.
3. Click buttons, navigate pages, submit forms, and verify persistence where relevant.
4. Check visible errors, console/runtime failures when available, and obvious layout breakage.
5. Produce pass/fail evidence with exact reproduction steps for defects.
6. Return a QA report and repair request when needed.

## Output Contract

- QA report artifact.
- Tested flows, pass/fail status, defects, screenshots when available, and repair recommendation.
- Dashboard-safe comment suitable for a review column or issue comment.

## Quality Rules

- Do not pass work based only on source inspection.
- Do not ignore broken buttons, placeholder flows, invisible text, or unusable layout.
- If browser execution is unavailable, clearly mark the evidence as limited.

## Failure And Repair

- Retry after Builder repairs the cited defects.
- Block when the app cannot start and the cause is environment/secrets rather than product code.
- Human approval is required when the QA result depends on external credentials or production data.

## Examples

### Good invocation

Input: Deployed app URL and requirements.
Output: QA report with tested flow, screenshot evidence, and repair findings.

### Bad invocation

Input: Generated app files.
Output: "Looks good" without opening or exercising the app.
