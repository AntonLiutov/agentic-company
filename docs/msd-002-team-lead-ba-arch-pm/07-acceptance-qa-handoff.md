# Acceptance, QA, and Handoff Strategy

## Purpose

MSD-002 must prove that the platform can coordinate work across agents, not just generate code. QA and Handoff must therefore validate both the generated application and the agentic delivery process.

## Acceptance dimensions

### 1. Platform behavior

The platform should prove:

- BA output is generated before Architect output;
- Architect output is generated before PM sprint plan;
- PM produces a bounded release plan and work board;
- Team Lead executes planned work one feature/task at a time;
- QA validates features;
- repair loop works;
- deployment is gated and owned by Deployment Agent when required;
- sprint-scoped handoff is generated;
- final report aggregates sprint evidence when the release is complete.

### 2. Generated project behavior

The generated app should prove:

- local run works;
- Docker run works;
- core user flow works;
- README is usable;
- tests exist and pass;
- deployment path exists or blocks with actionable reason.

### 3. Demo behavior

The demo should prove:

- a user can understand what happened;
- the console shows progress;
- evidence exists;
- final report is readable;
- the system looks like a delivery organization, not one giant prompt.

## QA strategy

QA should map:

```text
feature -> acceptance criterion -> check -> evidence -> owner if failed
```

Example:

```json
{
  "feature_id": "F2",
  "criterion": "Team Lead can call QA after Fullstack completes a feature.",
  "check": "unit/team_lead_calls_qa_after_fullstack",
  "evidence": ["tests/unit/agents/team_lead/test_feature_loop.py"],
  "failure_owner": "team-lead-agent"
}
```

## Platform tests

Required tests:

### Team Lead tests

- one feature passes;
- multiple features execute in order;
- QA failure creates fix request;
- Fullstack repair runs after QA failure;
- max repair attempts blocks sprint;
- deployment does not run before all QA passes;
- handoff runs after deployment;
- sprint result contains artifact references.

### PM tests

- PM creates a bounded release plan from input artifacts;
- every sprint has features;
- every feature has acceptance criteria;
- dependencies are serializable;
- Team Lead can consume PM sprint plan.

### Architect tests

- Architect writes required architecture artifacts;
- PM can consume architecture plan;
- implementation constraints are included in downstream prompt.

### BA tests

- BA writes requirements spec;
- BA writes acceptance criteria;
- BA writes edge cases and open questions;
- Architect can consume BA output.

### Human gate tests

- deployment approval pauses run;
- approval resumes run;
- rejection blocks deployment;
- blocker escalation writes artifact/event.

## Handoff strategy

Handoff supports two modes.

### Sprint report mode

Inputs:

- sprint plan;
- TeamLeadResult;
- completed features;
- QA results;
- deployment summary;
- evidence paths;
- blockers.

Output:

```text
runs/<run-id>/handoff/sprints/<sprint-id>/09-handoff-summary.md
runs/<run-id>/handoff/sprints/<sprint-id>/release-report.html
runs/<run-id>/handoff/sprints/<sprint-id>/release-evidence.json
```

Required sections:

```text
Status
Sprint Goal
Delivered Features
QA Summary
Deployment Summary
Evidence
Known Limitations
Next Recommended Action
```

### Final project report mode

Inputs:

- all sprint reports;
- final deployment summary;
- final QA summary;
- known limitations;
- public URL(s), if any.

Output:

```text
runs/<run-id>/handoff/project/09-handoff-summary.md
runs/<run-id>/handoff/project/release-report.html
runs/<run-id>/handoff/project/release-evidence.json
```

Required sections:

```text
Status
Project Summary
Delivered Sprints
Delivered Features
How To Use
Public URL(s)
QA Evidence
Deployment Evidence
Known Limitations
Recommended Next Steps
```

## Release acceptance

MSD-002 is releasable when:

- all new unit tests pass;
- at least one integration test runs BA -> Architect -> PM -> Team Lead with fake runners;
- at least one integration test runs Team Lead -> Fullstack -> QA repair loop;
- console can display sprint/feature/agent state;
- docs explain the milestone and next steps;
- final handoff can aggregate available sprint handoff evidence.
