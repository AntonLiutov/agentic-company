# MSD-002 - Team Lead Sprint Orchestration + BA / Architect / PM Planning Layer

## Status

Implemented as the current head-led delivery PoC. The milestone now represents
the baseline platform shape, not a future implementation plan.

## Purpose

This milestone upgrades the agentic delivery pipeline from a fixed sequence of
specialist agents into a more realistic delivery organization.

The previous platform shape had a useful execution lane:

```text
Planning -> Fullstack -> QA -> Deployment -> Handoff
```

MSD-002 replaces that legacy planning entrypoint with a Head-led coordination
layer:

```text
Head / Delivery Coordinator
  -> Business Analyst Agent
  -> Solution Architect Agent
  -> Project Manager Agent
  -> Team Lead Agent
       -> Fullstack Agent
       -> QA Agent
       -> Deployment Agent
       -> Handoff Agent
```

The key idea is to avoid premature free-form agent rooms. Head coordinates the
macro delivery flow, while **Team Lead Agent** coordinates sprint execution using
structured state, controlled tools, repair loops, and clear handoff artifacts.

## Implemented capability

1. `HeadAgent` coordinates BA, Architect, PM, and Team Lead through bounded tools.
2. `BusinessAnalystAgent` produces structured requirements artifacts.
3. `SolutionArchitectAgent` produces architecture, decisions, risks, and diagrams.
4. `ProjectManagerAgent` produces release planning, roadmap, work board, and handoff criteria.
5. `TeamLeadAgent` executes planned work through Fullstack, QA, Deployment, and Handoff.
6. Feature QA repair loops are bounded and evidence-based.
7. Handoff artifacts are sprint-scoped and project-scoped.
8. Console overview exposes current agent work, board state, events, and artifacts.
9. Azure/dev deployment is a supported delivery path owned by Deployment Agent.

## Why not agent rooms yet?

Free-form agent rooms are attractive, but they introduce cost, routing complexity, and noisy agent behavior too early. MSD-002 keeps the process predictable:

- Head controls the macro sequence.
- PM creates sprint plans.
- Team Lead controls feature execution.
- Agents report structured results.
- Human approval gates protect risky steps.

Agent rooms can later be added as a UI and event-stream visualization layer over the same execution model.

## Files in this milestone pack

| File | Purpose |
|---|---|
| `01-milestone-charter.md` | Scope, goals, non-goals, success criteria |
| `02-business-analysis.md` | BA work: requirements, roles, rules, open questions |
| `03-architecture-plan.md` | Architecture for Team Lead, PM, BA, Architect, runtime, state |
| `04-project-management-release-plan.md` | Sprint plan and delivery sequencing |
| `05-team-lead-agent-contract.md` | Team Lead agent responsibility, graph, loop, contracts |
| `06-runtime-tool-registry-human-gates.md` | Base agent architecture, runtimes, tools, pause/resume |
| `07-acceptance-qa-handoff.md` | Acceptance, tests, reporting and handoff strategy |
| `08-codex-implementation-prompt.md` | Copy-paste prompt for Codex |
| `09-task-breakdown.md` | Codex-ready task breakdown |
| `10-implementation-start.md` | Suggested first implementation slice and branch sequence |
| `agents/` | Proposed agent descriptors for BA, Architect, PM, and Team Lead |
| `schemas/` | Draft JSON contracts for sprint plans, feature tasks, and Team Lead results |

## Expected result after MSD-002

A user can provide a project brief, and the platform can:

1. produce BA requirements artifacts;
2. produce architecture artifacts;
3. produce a bounded release plan with one or more sprints;
4. execute planned sprint work through Team Lead;
5. implement each feature through Fullstack;
6. validate each feature through QA;
7. repair failed features through a controlled loop;
8. call Deployment Agent when the release/sprint calls for deployment;
9. create sprint handoff artifacts and, when appropriate, a final project report.
