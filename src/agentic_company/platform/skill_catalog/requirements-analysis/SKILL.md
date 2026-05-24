---
name: requirements-analysis
description: Clarify a product idea into a delivery-ready requirements brief. Use for Business Analyst work, acceptance criteria, user goals, constraints, and open questions. Do not use for architecture, implementation, deployment, or release reporting.
---

# Requirements Analysis

## Purpose

Turn a rough product idea into a business-readable requirements brief that later agents can implement and verify.

## Boundaries

- Owns user goals, scope, acceptance criteria, risks, and open questions.
- Does not choose detailed architecture, write code, deploy, or produce the final release story.
- Escalates when the request is missing essential business intent or requires human approval.

## Inputs

- Product idea or dictated request.
- Existing requirements, customer notes, or external work item references.
- Project type, scope size, risk mode, and provider constraints when available.

## Workflow

1. Identify the target user, product outcome, and smallest valuable demo.
2. Preserve concrete requested behavior, domain terms, links, data, and constraints.
3. Separate must-have requirements from assumptions and optional ideas.
4. Write acceptance criteria that QA can test without reading raw chat logs.
5. Record open questions and risks without blocking simple, reasonable progress.
6. Return a concise requirements brief with artifact references.

## Output Contract

- Status and business summary.
- Requirements brief artifact.
- Acceptance criteria, assumptions, risks, and open questions.
- Dashboard-safe comment for internal or external work boards.

## Quality Rules

- Do not invent product scope beyond the user's request.
- Do not drop concrete app behaviors, buttons, fields, reports, integrations, or deployment needs.
- Keep wording business-readable and implementation-neutral.

## Failure And Repair

- Retry when the brief misses concrete requested behavior.
- Block when product intent is contradictory or requires credentials/approval.
- Ask for human approval only for high-risk scope or irreversible decisions.

## Examples

### Good invocation

Input: "Build a task tracker with shared board, identity, and reports."
Output: Requirements brief with user goals, acceptance criteria, constraints, and open questions.

### Bad invocation

Input: "Build a task tracker."
Output: A full architecture proposal with invented enterprise features.
