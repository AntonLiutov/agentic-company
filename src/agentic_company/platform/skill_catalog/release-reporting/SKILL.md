---
name: release-reporting
description: Produce a business-readable release report from artifacts and delivery evidence. Use for Release Reporter or handoff work, final summary, generated app URL, limitations, next steps, and stakeholder-ready outcomes. Do not use for implementation or QA execution.
---

# Release Reporting

## Purpose

Explain what was built, how to try it, what evidence exists, and what should happen next.

## Boundaries

- Owns final business report, release summary, limitations, and next steps.
- Does not change code, rerun deployment, or hide unresolved blockers.
- Escalates when required release evidence is missing.

## Inputs

- Requirements, architecture, delivery plan, QA report, deployment summary, and artifact registry refs.
- Generated app URL and known limitations.
- Stakeholder audience and project visibility.

## Workflow

1. Read the business-facing and release artifacts first.
2. Summarize the original goal and delivered outcome.
3. Link the generated app, key artifacts, QA evidence, and deployment evidence.
4. State limitations, blockers, and recommended next steps plainly.
5. Avoid raw logs unless the audience explicitly needs developer diagnostics.
6. Return a release report artifact and dashboard-safe final comment.

## Output Contract

- Release report artifact.
- Business summary, app URL, evidence links, limitations, and next steps.
- Dashboard-safe completion comment for internal or external boards.

## Quality Rules

- Do not claim success if QA or deployment evidence is missing.
- Do not expose secrets, raw prompts, or internal debug files.
- Keep the report useful for a non-engineering stakeholder.

## Failure And Repair

- Retry when the report misses evidence or contradicts artifacts.
- Block when deployment/QA artifacts are absent from the registered evidence contract.
- Human approval is required for public-facing release claims in high-risk mode.

## Examples

### Good invocation

Input: Requirements, QA report, deployment URL.
Output: Release report with outcome, evidence, limits, and next steps.

### Bad invocation

Input: Failed deployment.
Output: Final report claiming the app is live.
