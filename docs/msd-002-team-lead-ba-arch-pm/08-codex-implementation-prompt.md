# Codex Implementation Prompt - MSD-002

This prompt is retained as historical implementation context. MSD-002 is now
implemented as the Head-led upstream planning and Team Lead delivery PoC. Use it
for maintenance or focused follow-up work, not as a from-scratch build prompt.

---

You are working in the `agentic-company` repository.

Implement **MSD-002 - Team Lead Sprint Orchestration + BA / Architect / PM Planning Layer**.

Read these milestone docs first:

- `docs/msd-002-team-lead-ba-arch-pm/README.md`
- `docs/msd-002-team-lead-ba-arch-pm/01-milestone-charter.md`
- `docs/msd-002-team-lead-ba-arch-pm/02-business-analysis.md`
- `docs/msd-002-team-lead-ba-arch-pm/03-architecture-plan.md`
- `docs/msd-002-team-lead-ba-arch-pm/04-project-management-release-plan.md`
- `docs/msd-002-team-lead-ba-arch-pm/05-team-lead-agent-contract.md`
- `docs/msd-002-team-lead-ba-arch-pm/06-runtime-tool-registry-human-gates.md`
- `docs/msd-002-team-lead-ba-arch-pm/07-acceptance-qa-handoff.md`
- `docs/msd-002-team-lead-ba-arch-pm/09-task-breakdown.md`

## Important context

The repository now has Head, Business Analyst, Architect, Project Manager, Team
Lead, Fullstack, QA, Deployment, Handoff, LangGraph delivery orchestration,
platform artifacts/events/state, Codex integrations, and a Streamlit console.
Do not rewrite working areas unless required.

## Primary goal

Maintain and improve the Head-led delivery flow. Head coordinates BA,
Architect, PM, and Team Lead. Team Lead coordinates Fullstack, QA, Deployment,
and Handoff using artifacts, work board state, bounded repair loops, and
explicit artifact refs.

## Implementation principles

1. Keep Head-led orchestration bounded and artifact-driven.
2. Do not introduce free-form agent chat rooms yet.
3. Use structured artifacts and state updates.
4. Add tests before or alongside implementation.
5. Reuse existing agent wrapper patterns.
6. Preserve existing public CLI/console behavior unless extending it safely.
7. Do not remove existing QA, deployment, Codex, or handoff evidence behavior.
8. Do not add destructive cloud operations.
9. Deployment remains approval-gated.
10. Prefer small, reviewable changes.

## Required implementation

### 1. Platform models

Add shared models for:

- `SprintPlan`
- `FeatureTask`
- `SprintExecutionState`
- `TeamLeadResult`
- `Escalation`
- `HumanGate` if not already represented elsewhere

Suggested locations:

- `src/agentic_company/platform/sprints.py`
- `src/agentic_company/platform/escalations.py`
- `src/agentic_company/platform/human_gates.py`

### 2. Team Lead Agent

Add:

```text
src/agentic_company/agents/team_lead/
  __init__.py
  agent.py
  graph.py
  models.py
  sprint_runner.py
  feature_loop.py
```

Team Lead must:

- accept one sprint plan;
- execute features sequentially;
- call Fullstack Agent for each feature;
- call QA Agent after each feature;
- route QA failures back to Fullstack up to max repair attempts;
- block/escalate after attempts are exhausted;
- call Deployment Agent only after all sprint features pass QA;
- call Handoff Agent in `sprint_report` mode after deployment;
- write structured sprint result artifacts.

### 3. Project Manager Agent

Maintain the PM Agent that generates bounded release plans from requirements and architecture artifacts.

Suggested location:

```text
src/agentic_company/agents/project_manager/
```

The first version may be deterministic or structured-output oriented. It should not be a fully autonomous agent yet.

### 4. Solution Architect Agent

Add an initial Architect Agent that writes architecture artifacts:

- `architecture-plan.md`
- `technical-decisions.md`
- `risks.md`
- `implementation-constraints.md`

Suggested location:

```text
src/agentic_company/agents/architecture/
```

### 5. Business Analyst Agent

Add an initial BA Agent that writes:

- `requirements-spec.md`
- `user-stories.json`
- `acceptance-criteria.json`
- `edge-cases.md`
- `open-questions.md`

Suggested location:

```text
src/agentic_company/agents/business_analysis/
```

### 6. Top-level graph integration

Maintain the active execution path:

```text
head -> business_analysis -> architecture -> project_management -> team_lead -> delivery gates -> handoff -> head completion
```

The old deterministic Planning Agent path is not the active graph.

### 7. Console updates

Expose at minimum:

- current sprint;
- current feature;
- current active agent;
- feature statuses;
- sprint result;
- blockers;
- pending human gates;
- sprint report links;
- final report link.

### 8. Tests

Add tests for:

- Team Lead one-feature pass;
- Team Lead multi-feature pass;
- QA failure routes to Fullstack repair;
- max repair attempts blocks sprint;
- deployment runs only after all features pass;
- handoff runs after deployment;
- PM creates a valid bounded release plan;
- BA/Architect/PM artifacts can be consumed downstream;
- graph sequence can run with fake runners.

## Definition of done

- `uv run --extra dev ruff format .` passes or changes are formatted.
- `uv run --extra dev ruff check .` passes.
- `uv run --extra dev pytest` passes or failing tests are documented with precise reasons.
- README/milestone docs are updated if behavior changes.
- Existing functionality is not broken.
- The final implementation summary explains what was added and how to run it.
