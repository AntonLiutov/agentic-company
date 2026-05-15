# MSD-001 Milestone Charter

## Executive Summary

MSD-001 turns the current single-app delivery PoC into a more realistic
agent-of-agents delivery workflow.

The platform should accept a product request with two small features, plan those
features as work items, generate a simple multi-service project, test the project
against the original requirements, repair failures through the Fullstack Agent,
deploy the result to stable dev resources, run deployment-owned smoke
validation, and hand off the result with clear business-facing evidence.

Independent QA-owned post-deployment validation and post-deploy repair routing
remain the next architecture milestone, where agents will communicate through a
richer AgentExecutor/inter-agent handoff model.

This is not a milestone for adding lots of product complexity. It is a milestone
for proving delivery complexity.

## Business Goal

Prove that the platform can deliver a miniature product increment, not only a
single generated file or toy app.

The business user should be able to say:

> Build a tiny system with an API and a web UI. It should support two simple
> features. Test it, deploy it, and tell me what was delivered.

The platform should coordinate the work without the user manually stitching
together planning, implementation, QA, deployment, and handoff.

## Product Goal

Deliver a generated application with this target shape:

```text
generated-project/
  api/
    app.py
    tests/
  web/
    app.py
  shared/
    ...
  docker-compose.yml
  Dockerfile.api
  Dockerfile.web
  pyproject.toml
  uv.lock
  README.md
```

The exact internals may evolve, but the project must have:

- one API service,
- one web UI service,
- a stable local Docker Compose setup,
- stable image/service/container naming,
- two small features or work items,
- tests/evidence that prove the web and API work together,
- deployment and handoff artifacts that describe what happened.

## Example Product Request

Use a request similar to this as the milestone acceptance scenario:

```text
Create a small internal task tracker.

Feature 1:
- The API can create and list tasks.
- The web UI can submit a task title and show the task list.

Feature 2:
- The API can mark a task as done.
- The web UI can toggle a task between open and done.

Use a simple in-memory store for now.
Run locally with Docker Compose.
Deploy to Azure dev resources after QA passes.
```

The example is intentionally simple. The complexity is in the delivery workflow:
planning, implementation conventions, QA strategy, repair loop, topology-aware
deployment, and handoff.

## End-To-End Workflow

Target workflow:

```text
requirements
  -> Planning Agent
  -> feature queue
  -> Fullstack Agent implements F1
  -> Fullstack Agent implements F2 after F1
  -> QA Agent creates implementation QA strategy and runs checks
  -> if implementation QA failed:
       QA fix request -> Fullstack repair -> QA rerun
  -> Deployment Agent inspects topology and deploys
  -> Deployment Agent runs deployment-owned smoke validation
  -> Handoff Agent writes delivery report
```

Near-term implementation must feed features to Fullstack one at a time. The
architecture should represent the work as feature items so QA, repair,
deployment, and handoff can attach evidence and decisions to the right feature
instead of receiving one vague implementation batch.

The important point is ownership: QA does not repair code, Deployment does not
pretend to be QA, and Fullstack does not decide whether its own work is
acceptable. The graph coordinates those specialist loops.

## Scope

### In Scope

- richer planning artifacts that include feature work items,
- stricter Fullstack Agent generation conventions,
- generated API + web app project archetype,
- stable Docker Compose naming for repeatable dev runs,
- QA strategy derived from requirements and generated topology,
- QA-generated or QA-selected checks for API, web, integration, Docker, and
  browser behavior,
- Fullstack repair loop with a maximum of 3 attempts,
- deployment-owned smoke validation after Azure publish/update,
- topology-aware deployment planning for the first supported multi-service
  shape,
- handoff report that includes usage, evidence, public URL, QA status, deployed
  resources, and known limitations,
- console/run artifacts that make the feature delivery readable.

### Out Of Scope

- production multi-tenant auth,
- real database persistence beyond a simple local/in-memory option,
- arbitrary cloud provider support,
- Kubernetes,
- fully parallel multi-agent execution,
- billing, cost limits, and quotas beyond basic visible warnings,
- supporting every possible generated topology,
- production-grade QA self-healing beyond the first Codex-owned QA contract.
- QA-owned post-deployment validation and post-deploy repair routing, which are
  deferred to the next AgentExecutor/inter-agent milestone.

## Architecture Goal

The milestone should preserve the Stage 6 ownership model:

```text
orchestration/
  delivery graph and routing

agents/
  planning/
  fullstack/
  quality/
  deployment/
  handoff/

integrations/
  codex/
  docker/
  azure/
  playwright/
  streamlit/

platform/
  state
  events
  artifacts
  security
```

New logic should go into the agent that owns the decision.

Reusable tool mechanics should go into integrations only when the behavior is
likely to be reused by more than one agent.

## Agent Maturity Target

This milestone does not need every agent to be fully autonomous. It should move
the core agents one maturity level forward.

| Agent | Current State | MSD-001 Target |
| --- | --- | --- |
| Planning | deterministic pipeline with useful artifacts | creates feature queue and richer technical brief |
| Fullstack | Codex-backed app generator | follows strict multi-service project conventions and repair inputs |
| QA | generic guardrails plus Codex review boundary | plans executable checks, runs evidence, judges results, and writes fix requests |
| Deployment | Azure Container Apps deterministic runner | chooses supported deployment path based on generated topology |
| Handoff | markdown summary | business-quality delivery report with evidence and URL |

## Definition Of Done

The milestone is done when one run can prove:

1. Requirements contain two simple features.
2. Planning produces a feature queue.
3. Fullstack generates an API + web project with stable naming.
4. QA validates API, web, API-web integration, Docker runtime, and browser flow.
5. QA failures can route through Fullstack repair up to 3 times.
6. Deployment detects the generated topology and deploys the supported dev shape.
7. Deployment-owned smoke validation confirms the deployed dev runtime is
   reachable and basically healthy.
8. Full QA Agent ownership of post-deployment validation and post-deploy failure
   routing is explicitly deferred to the next AgentExecutor/inter-agent
   communication milestone.
9. Handoff includes a public URL, screenshots/evidence links, feature summary,
   QA summary, deployment summary, known limitations, and next steps.
10. The console/live logs make it clear which feature/stage/agent is active.
11. Tests cover the new planning contracts, QA strategy generation, deployment
    topology detection, and handoff report content.

## Intentional Deferral

MSD-001 currently treats deployment-owned smoke validation as enough to prove the
deployed dev runtime is reachable and basically healthy. Full QA Agent ownership
of post-deployment validation is deferred to the next architecture milestone,
where lower-level agents move toward AgentExecutor-style delegation and richer
inter-agent communication.

That future milestone should let Deployment hand public runtime targets to QA,
QA independently classify deployed-runtime failures, and the company graph route
the result to Fullstack or Deployment based on QA's diagnosis.
