# MSD-002 Milestone Charter

## Name

**MSD-002 - Team Lead Sprint Orchestration + BA / Architect / PM Planning Layer**

## Background

The previous system had a narrow but useful delivery lane with Planning,
Fullstack, QA, Deployment, and Handoff agents. MSD-002 removes the legacy
Planning Agent from the active graph and replaces it with a Head-led upstream
planning and delivery coordination flow.

## Problem

The current delivery graph can execute a project, but its agent order is still mostly fixed and pipeline-like. It does not yet express a realistic delivery organization where:

- requirements are refined before architecture;
- architecture constrains implementation;
- PM plans work into sprints;
- Team Lead coordinates feature-by-feature execution;
- QA failures route back to the correct owner;
- deployment happens after a sprint is actually ready;
- handoff can summarize sprint-level and final project evidence.

## Goal

Implement a milestone that introduces:

1. **Head Agent** - coordinates upstream planning and delivery handoff between agents.
2. **Business Analyst Agent** - turns raw product intent into requirements, stories, rules, and edge cases.
3. **Solution Architect Agent** - produces technical constraints and architecture artifacts.
4. **Project Manager Agent** - converts scope into a bounded release plan and work board.
5. **Team Lead Agent** - coordinates sprint execution using Fullstack, QA, Deployment, and Handoff.
6. **Human gates** - pause for approval, escalation, deployment confirmation, and blocker resolution where needed.

## Non-goals

Do not implement these in MSD-002:

- full free-form agent chat rooms;
- Telegram / Slack integration;
- multi-provider model routing;
- production-grade multi-tenant SaaS;
- complex RAG product generation;
- WebSocket app as the main target;
- unbounded free-form Head Agent that can call any tool without role and recovery policy.

These are later milestones.

## Target operating model

```text
Human / User
  -> Head / Delivery Coordinator
  -> Business Analyst Agent
  -> Solution Architect Agent
  -> Project Manager Agent
  -> for each sprint:
       Team Lead Agent
         -> for each feature:
              Fullstack Agent
              QA Agent
              repair loop if needed
         -> Deployment Agent
         -> Handoff Agent: sprint report
  -> Handoff Agent: final project report
  -> Human / User
```

## Success criteria

The milestone is successful when:

- a bounded release plan can be generated from a project brief;
- Team Lead can execute planned sprint work feature-by-feature;
- QA can fail a feature and Team Lead routes it back to Fullstack;
- max repair attempts block the sprint with clear evidence;
- Deployment Agent is called when the sprint/release calls for deployment and can deploy or return an evidenced blocker;
- Handoff can create sprint-scoped artifacts;
- final handoff can aggregate sprint evidence when the release is complete;
- Streamlit console exposes current sprint, current feature, active agent, status, blockers, and approval needs;
- tests cover the key orchestration behavior.

## Demo scenario

Use a small target product, not a large complex app:

**Multi-Service Task Tracker**

Minimal product scope:

- API service;
- web UI service;
- create/list tasks;
- mark tasks done;
- Docker-ready project;
- deployable to Azure Container Apps.

This target product is intentionally small. The demo should prove the agentic
delivery system, not only Codex app generation.
