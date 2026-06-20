---
name: sprint-planning
description: Break requirements and architecture into delivery work items. Use for Delivery Planner work, sprint slicing, dependencies, stage mapping, and acceptance gates. Do not use for code implementation, QA execution, or final handoff.
---

# Sprint Planning

## Purpose

Turn requirements and architecture into ordered work items that agents can execute, verify, and report.

## Boundaries

- Owns sprint structure, feature slicing, dependencies, acceptance gates, and delivery risks.
- Does not write implementation code or mark QA complete.
- Escalates when scope is too large for the selected project size or risk mode.

## Inputs

- Requirements brief.
- Architecture report.
- Project type, scope size, risk mode, and expected deployment target.

## Workflow

1. Identify the smallest end-to-end product slice.
2. Split work into clear feature/work item IDs with acceptance criteria.
3. Order dependencies so Builder and QA do not guess.
4. Define release gates and evidence required for completion.
5. Keep the plan compact enough for the selected scope size.
6. Return a delivery plan artifact with board-ready work items.

## Output Contract

- Delivery plan artifact.
- Work item list, dependencies, stages, acceptance gates, and risk notes.
- Dashboard-safe status/comment for internal or external boards.

## Quality Rules

- Each work item must be independently understandable.
- Avoid vague tasks like "polish UI" without testable criteria.
- Do not create more work than the scope size can support.

## Failure And Repair

- Retry when work items are too broad or missing acceptance gates.
- Block when requirements and architecture contradict each other.
- Human approval is required for high-risk or expanded scope.

## Examples

### Good invocation

Input: Requirements and architecture for a task board.
Output: Sprint plan with feature IDs, dependencies, and acceptance gates.

### Bad invocation

Input: Simple prototype.
Output: Ten vague epics with no QA criteria.
