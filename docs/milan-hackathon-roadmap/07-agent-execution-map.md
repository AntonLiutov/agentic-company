# 07 Agent Execution Map

This file translates the roadmap into the agent team we want to build. It defines what each agent receives, decides, produces, and hands off.

## Operating Principle

Agents should communicate through artifacts, not hidden memory.

Every major step should answer:

- What did this agent read?
- What did this agent decide?
- What artifact did this agent write?
- What agent or system consumes the artifact next?

## Agent Flow

```text
Raw Requirements
-> Intake Agent
-> Product Owner Agent
-> Business Analyst Agent
-> Team Assembler Agent
-> Architecture Agent
-> PM / Delivery Manager Agent
-> Design Agent
-> Fullstack Engineer Agent
-> QA Agent
-> Deployment Agent
-> Documentation / Handoff Agent
-> Demo / Pitch Agent
```

The first implementation can keep several of these agents deterministic or merged. The product demo should still show differentiated roles and artifacts.

## Agent Responsibilities

| Agent | Input | Decision | Output | Next Consumer |
| --- | --- | --- | --- | --- |
| Intake Agent | Raw user requirements | What is known, missing, and unclear | `01-intake-brief.json` | Product Owner, BA |
| Product Owner Agent | Intake brief | MVP goal, users, non-goals, acceptance criteria | `02-product-scope.md` | BA, Architecture |
| Business Analyst Agent | Intake brief, product scope | Requirements, edge cases, constraints | `03-requirements-analysis.md` | Architecture, PM |
| Team Assembler Agent | Brief, classification | Smallest useful team | `04-staffing-decision.json` | PM |
| Architecture Agent | Scope, requirements | System shape, stack, risks, boundaries | `05-architecture-plan.md` | PM, Engineering |
| PM / Delivery Manager Agent | Scope, staffing, architecture | Phases, tasks, dependencies, risks | `06-delivery-plan.md` | Engineering, QA |
| Design Agent | Product scope, target users | UX flow and screen structure | `07-design-brief.md` | Engineering |
| Fullstack Engineer Agent | Implementation brief, design, architecture | Code changes needed | Generated project files | QA |
| QA Agent | Generated project, acceptance criteria | Whether output is ready for deployment or repair | `08-qa-report.md`, `10-fix-request.md` when failed | Deployment or Fullstack Engineer |
| Deployment Agent | Passing QA output, generated project, `.env`, deployment request | Whether the generated app can be published | `11-deployment-plan.*`, `12-deployment-request.*`, `13-deployment-summary.md` | Handoff |
| Documentation / Handoff Agent | All artifacts, generated project, deployment summary | How to use the live app and what remains | `09-handoff-summary.md` | User, judges |
| Demo / Pitch Agent | Product outputs, business story | Best demo narrative | `demo-script.md`, `slides-outline.md` | Presentation |

## Weekend Simplification

For speed, combine the first version into these visible stages:

| Visible Stage | Internal Agents Represented |
| --- | --- |
| Intake | Intake Agent |
| Scope | Product Owner Agent, Business Analyst Agent |
| Staffing | Team Assembler Agent |
| Plan | Architecture Agent, PM Agent, Design Agent |
| Execution | Fullstack Engineer Agent through Codex |
| Review | QA Agent |
| Deployment | Deployment Agent |
| Handoff | Documentation / Handoff Agent |

This keeps the UI understandable while preserving the future agent architecture.

## Artifact Contract For The Next Vertical Slice

The next slice should produce:

```text
runs/<run-id>/
  01-intake-brief.json
  02-project-classification.json
  03-staffing-decision.json
  04-workflow-plan.json
  05-implementation-brief.md
  06-execution-request.json
  07-execution-summary.md
  08-qa-report.md
  10-fix-request.md
  11-deployment-plan.md
  11-deployment-plan.json
  12-deployment-request.md
  12-deployment-request.json
  13-deployment-summary.md
  09-handoff-summary.md
  qa/
    results.json
    commands.log
    docker/build-summary.json
  deployment/
    commands.log
    browser/post-deploy-chat-transcript.json
  codex/
    prompt.md
    execution.log
    events.jsonl
  events.jsonl
```

The numbered artifacts are the user-facing handoff chain. `09-handoff-summary.md` is written only
after deployment succeeds. Codex prompt/log/event telemetry should stay under `codex/`, QA evidence
under `qa/`, and deployment evidence under `deployment/` so the top-level run folder remains
readable.

## Codex Worker Boundary

The Fullstack Engineer Agent should not receive vague chat context. It should receive:

- Agent role definition
- Implementation brief
- Architecture notes
- Design brief if present
- Target project directory
- Explicit expected files
- Safety constraints
- Completion criteria

Expected first prompt shape:

```text
You are the Fullstack Engineer Agent.

Read the implementation brief and create the smallest project that satisfies it.
Work only inside the target project directory.
Do not add deployment, auth, database, or Docker unless requested.
Write a short execution summary when complete.
```

## Event Types

Keep event names simple:

- `run_started`
- `agent_started`
- `artifact_written`
- `decision_recorded`
- `agent_completed`
- `agent_failed`
- `execution_started`
- `execution_completed`
- `qa_started`
- `qa_completed`
- `fix_request_created`
- `deployment_started`
- `deployment_completed`
- `deployment_started`
- `deployment_completed`
- `handoff_started`
- `handoff_ready`
- `run_completed`

Future events:

- `human_approval_requested`
- `human_approval_granted`
- `qa_failed`

## Demo Narrative From Agent Flow

The demo should not say "we call a model several times." It should say:

> We assemble an AI delivery team. Each agent has a job, writes an artifact, and hands it to the next role. The system can then ask a Codex engineer agent to implement the plan in a real project folder.

That is the story that maps to Milan's agentic workflow and collaborative systems tracks.
