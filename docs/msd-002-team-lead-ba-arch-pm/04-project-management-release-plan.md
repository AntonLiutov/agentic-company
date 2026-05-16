# Project Management Release Plan

## Role

**Project Manager Agent**

## Mission

Convert approved requirements and architecture artifacts into a small release plan with sprint goals, feature sequencing, dependencies, acceptance criteria, and clear execution packages for Team Lead.

## Inputs

- `upstream-planning/business-analyst/` artifacts
- `upstream-planning/architect/` artifacts
- current platform constraints
- available agent roster and ownership boundaries
- target demo deadline

## Outputs

```text
runs/<run-id>/upstream-planning/project-management/
  release-plan.json
  release-plan.md
  roadmap.csv
  work-board.json
  dod.md
  risks-and-dependencies.md or equivalent sections
```

## Release strategy

Use a small bounded release plan first. One or more sprints are acceptable when
the scope justifies them; do not split work into tiny tasks just to create more
sprints. The goal is to prove that the agentic company can plan, execute, QA,
deploy when required, and hand off with evidence.

## Sprint 1 - Team Lead Sprint Execution

### Sprint goal

Introduce Team Lead Agent and make it coordinate Fullstack, QA, Deployment, and Handoff for one sprint.

### Features

#### F1 - Sprint and feature models

Description:
Add shared models for sprint plans, feature tasks, team lead results, repair attempts, and escalations.

Acceptance criteria:

- SprintPlan can represent sprint goal, features, exit criteria, deployment policy, and final-sprint flag.
- FeatureTask can represent acceptance criteria, dependencies, QA notes, deployment notes, and status.
- TeamLeadResult can summarize completed/failed features and blockers.
- Unit tests validate model serialization and basic validation.

#### F2 - Team Lead Agent wrapper

Description:
Create first-class TeamLeadAgent with descriptor and state-in/state-out run contract.

Acceptance criteria:

- TeamLeadAgent exists under `agents/team_lead/`.
- It can accept a sprint plan from delivery state.
- It writes a structured sprint execution result.
- It can be injected/tested with fake Fullstack/QA/Deployment/Handoff runners.

#### F3 - Feature-by-feature execution loop

Description:
Team Lead executes sprint features sequentially.

Acceptance criteria:

- Team Lead selects next uncompleted feature.
- Team Lead calls Fullstack for that feature.
- Team Lead calls QA after Fullstack.
- Passing QA marks the feature complete.
- More features continue until sprint completion.

#### F4 - QA repair loop

Description:
Team Lead routes failed QA back to Fullstack up to a max attempt limit.

Acceptance criteria:

- QA failure creates structured fix request.
- Fullstack receives feature-specific repair input.
- QA reruns after repair.
- Max attempts block the sprint with evidence.

#### F5 - Sprint deployment and sprint report

Description:
Team Lead triggers deployment only after all sprint features pass, then requests Handoff sprint report.

Acceptance criteria:

- Deployment does not run before all feature QA passes.
- Deployment can require human approval.
- Handoff can run in `sprint_report` mode.
- Sprint result includes deployment and handoff artifact references.

## Sprint 2 - BA / Architect / PM Planning Layer

### Sprint goal

Add upstream planning agents and connect them into the Head-led delivery sequence.

### Features

#### F6 - Project Manager Agent

Description:
PM Agent creates a bounded release plan from requirements and architecture artifacts.

Acceptance criteria:

- PM reads BA and Architect artifacts.
- PM writes `release-plan.json` and `release-plan.md`.
- PM writes sprint plans consumable by Team Lead.
- PM includes dependencies and exit criteria.

#### F7 - Solution Architect Agent

Description:
Architect Agent creates technical plans and constraints for implementation.

Acceptance criteria:

- Architect writes architecture plan, technical decisions, risks, and implementation constraints.
- PM can consume Architect artifacts.
- Fullstack prompt receives relevant constraints.

#### F8 - Business Analyst Agent

Description:
BA Agent creates requirements artifacts from raw product brief.

Acceptance criteria:

- BA writes requirements spec, user stories, acceptance criteria, edge cases, and open questions.
- Architect can consume BA output.
- QA can derive tests from BA acceptance criteria.

#### F9 - Deterministic top-level graph

Description:
Add a top-level sequence that runs BA -> Architect -> PM -> Team Lead per sprint -> final handoff.

Acceptance criteria:

- The graph can run the new sequence in tests.
- Each sprint is passed to Team Lead separately.
- Final handoff can aggregate sprint reports.

#### F10 - Human gates and console visibility

Description:
Expose key approval and blocker states in the console.

Acceptance criteria:

- Console shows current sprint, current feature, active agent, and blocker status.
- Console can represent approval-required states.
- Deployment approval remains explicit.
- Human blocker input can be recorded as artifact/event.

## Suggested execution phases

### Phase 1 - Team Lead foundation

Focus: Team Lead Agent.

- F1, F2, F3, F4.
- F5 if time allows.

### Phase 2 - PM and sprint planning

Focus: PM and sprint planning.

- F5 if not done.
- F6.
- Start F7.

### Phase 3 - BA/Architect integration

Focus: BA/Architect integration and end-to-end run.

- F7, F8, F9.
- Start F10.

### Phase 4 - Demo polish

Focus: demo polish.

- F10.
- console polish;
- Azure deployment run;
- one strong generated demo app.

### Phase 5 - Pitch/readiness

Focus: pitch/readiness.

- demo video;
- pitch deck;
- unit economics;
- roadmap;
- final docs.

## PM acceptance

PM work is complete when:

- release plan has the smallest credible number of sprints for the scope;
- every sprint has clear feature tasks;
- every feature has acceptance criteria;
- dependencies are explicit;
- Team Lead can execute sprint plans without guessing;
- final handoff can identify sprint reports for aggregation.
