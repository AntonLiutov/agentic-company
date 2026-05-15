# Agent Workflows And Contracts

This document describes how the specialist agents should cooperate during
MSD-001.

The current platform has a working delivery graph. This milestone should make
the agents more capable without breaking the clean ownership model.

## High-Level Delivery Graph

Target company graph:

```text
requirements
  -> planning
  -> for each feature in release batch:
       fullstack
       implementation_qa
  -> route_after_qa

route_after_qa:
  release batch passed -> deployment
  failed and attempts left -> fullstack_repair -> implementation_qa
  failed and attempts exhausted -> blocked

deployment
  -> deployment_smoke
  -> route_after_deployment

route_after_deployment:
  deployed_and_smoke_passed -> handoff
  blocked or failed -> deployment_repair_or_blocked
```

The top-level graph should remain readable. Internal details belong inside each
agent graph.

The platform is therefore not a one-way chain. MSD-001 has one active
specialist feedback loop and one intentionally deferred loop:

1. `Fullstack <-> QA` for implementation correctness before deployment.
2. Future `Deployment <-> QA`, with possible return to Fullstack, for deployed
   runtime correctness after deployment.

QA owns implementation validation and evidence. Fullstack owns
code/application repairs. Deployment owns infrastructure, target topology,
runtime configuration, image publishing, redeploys, and deployment smoke
evidence.

Deployment is normally release-batch based. The release candidate is deployed
after the planned feature queue passes implementation QA. Planning or Deployment
may choose a different strategy, such as `per_feature`, `hotfix`, or
`preview_per_feature`, only when the delivery plan explicitly calls for it.

MSD-001 implementation note: true QA-owned `post_deploy_qa` is deferred to the
next AgentExecutor/inter-agent-communication milestone. For MSD-001 closure,
Deployment owns smoke evidence after publishing and the console labels that
evidence as deployment-owned instead of presenting it as a separate QA Agent
pass.

## Planning Agent

### Responsibility

Planning turns raw requirements into an executable delivery plan.

For MSD-001, Planning must stop treating the request as one blob. It should
produce a feature queue that downstream agents can reference.

### Inputs

- `00-requirements.md`
- current project archetype rules
- available agent registry
- platform constraints

### Outputs

- `01-intake-brief.json`
- `02-project-classification.json`
- `03-staffing-decision.json`
- `04-workflow-plan.json`
- `05-implementation-brief.md`
- `06-execution-request.json`
- feature queue embedded in workflow/execution artifacts

### Feature Item Contract

Initial feature item fields:

```json
{
  "id": "F1",
  "title": "Create and list tasks",
  "user_value": "A user can capture work and see what exists.",
  "acceptance_criteria": [
    "API can create a task with a title.",
    "API can list tasks.",
    "Web UI can submit a task title.",
    "Web UI shows the current task list."
  ],
  "dependencies": [],
  "delivery_order": 1,
  "suggested_owner_agent": "fullstack-agent",
  "test_notes": [
    "Verify API create/list endpoints.",
    "Verify web submit/list flow through browser."
  ],
  "deployment_notes": [
    "Web service must reach API service through configured base URL."
  ]
}
```

### Planning Acceptance

- Each feature has testable acceptance criteria.
- Dependencies are explicit.
- The implementation brief tells Fullstack which features are in scope.
- The QA notes give QA enough context to derive checks.
- The deployment notes mention topology-relevant constraints.

## Fullstack Agent

### Responsibility

Fullstack implements the selected feature batch and generated project.

For MSD-001, Fullstack must follow strict conventions so QA and Deployment can
reason about the output.

### Inputs

- `05-implementation-brief.md`
- `06-execution-request.json`
- feature queue
- previous QA fix request, if this is a repair
- run-local `.env`, if present

### Outputs

- generated project files,
- `07-execution-summary.md`,
- Codex prompt/log/events artifacts,
- stable Docker Compose setup.

### Required Multi-Service Conventions

When the plan asks for API + web, Fullstack should use this layout:

```text
generated-project/
  api/
    app.py
    tests/
  web/
    app.py
  docker-compose.yml
  Dockerfile.api
  Dockerfile.web
  pyproject.toml
  uv.lock
  README.md
```

Required naming:

```text
api service: api
web service: web
api container: agentic-api-dev
web container: agentic-web-dev
api image: agentic-api:latest
web image: agentic-web:latest
compose project: agentic-dev
```

### Fullstack Prompt Rules

Fullstack prompt should say:

- preserve existing `.env`;
- do not invent extra containers;
- do not generate random image, container, or compose project names;
- do not bake secrets into Docker images;
- use `.env.example` for expected variables;
- use `uv.lock` and cache-friendly Dockerfiles;
- copy dependency metadata before app source in Dockerfiles;
- use stable API base URL configuration for the web service;
- keep the functionality small and aligned to feature IDs;
- write README instructions for local, Docker, and deployment assumptions;
- summarize implemented feature IDs in `execution-summary.md`.

### Repair Contract

When implementation QA finds an application behavior failure, Fullstack repair
input should include:

- failed feature IDs,
- failed acceptance criteria,
- failed check names,
- evidence paths,
- relevant logs,
- exact expected behavior,
- previous implementation summary,
- instruction to make the smallest fix.

Fullstack should not rewrite the whole project unless QA evidence shows the
architecture is wrong.

Fullstack should not repair Azure resources, registry authentication, Container
App ingress, or deployment environment configuration. Those failures belong to
Deployment.

## QA Agent

### Responsibility

QA validates that the generated project satisfies requirements.

For MSD-001, QA must create a strategy from requirements, feature queue, and
project topology before running checks.

### Inputs

- requirements,
- planning artifacts,
- generated project files,
- execution summary,
- previous QA results, if this is a rerun.

### Outputs

- `qa/strategy.json`
- `qa/strategy.md`
- `qa/test-plan.json`
- `qa/results.json`
- `08-qa-report.md`
- command logs,
- screenshots/transcripts,
- `10-fix-request.*` when failed.

### QA Strategy Contract

QA strategy should map:

```text
feature -> acceptance criterion -> check group -> evidence artifact
```

Example:

```json
{
  "feature_id": "F1",
  "criterion": "Web UI can submit a task title.",
  "check_group": "browser",
  "check_name": "web_submit_task_flow",
  "evidence": [
    "qa/screenshots/web-submit-task.png",
    "qa/browser/task-flow-transcript.json"
  ]
}
```

### QA Modes

Quality Agent should support at least two modes:

```text
implementation_qa
post_deployment_qa
```

`implementation_qa` validates the generated project before deployment.
`post_deployment_qa` validates the deployed public runtime after Deployment
publishes or updates resources.

Both modes should use the same principle:

```text
feature -> acceptance criterion -> check group -> evidence artifact -> owner if failed
```

### QA Codex Evidence Ownership

The platform must not maintain a predefined list of QA check groups. QA Codex
owns the choice of evidence for each active feature.

For any generated project, QA Codex should infer the right evidence from:

- active feature acceptance criteria,
- completed features that must not regress,
- generated implementation,
- planning artifacts,
- deployment/runtime constraints.

QA Codex may choose static inspection, dependency checks, runtime checks,
browser checks, Docker checks, generated scripts, screenshots, transcripts, or
other evidence when they are relevant. Those choices are made by the agent, not
by hardcoded platform QA code.

### QA Failure Contract

When failed, QA should write a fix request that includes:

- failed feature IDs,
- failed acceptance criteria,
- failed checks,
- why it failed,
- evidence paths,
- likely owner agent,
- suggested repair focus,
- whether failure blocks deployment.

QA should not include secrets in fix requests.

QA should classify failure ownership:

| Failure Type | Owner | Next Step |
| --- | --- | --- |
| missing artifact, broken endpoint, wrong UI behavior | Fullstack Agent | app repair then QA rerun |
| Docker Compose cannot build because generated project is wrong | Fullstack Agent | app/container definition repair then QA rerun |
| Azure login, registry auth, resource naming, ingress, env wiring | Deployment Agent | deployment repair/retry; future post-deploy QA rerun |
| deployed app returns wrong behavior despite healthy infrastructure | Fullstack Agent | future app repair, implementation QA, deployment update, post-deploy QA |

## Deployment Agent

### Responsibility

Deployment takes a QA-passed generated project and makes it available in a
stable dev cloud environment.

For MSD-001, Deployment is a Codex-owned specialist. The platform does not
classify topology, choose fixed service names, or prepare a fixed Azure command
sequence. Deployment Codex inspects Docker Compose, Dockerfiles, source layout,
environment examples, README instructions, and QA evidence, then deploys or
blocks with a precise reason. Deployment reports deployment status, public
URL(s), and deployment-owned smoke evidence. It does not self-certify full
product correctness after publishing; future QA-owned public-runtime validation
belongs to Quality Agent in `post_deployment_qa` mode after the next
AgentExecutor/inter-agent milestone.

### Inputs

- generated project,
- deployment request,
- feature queue,
- QA status and evidence,
- run-local environment variables,
- Azure CLI account,
- Docker availability.

### Outputs

- `11-deployment-plan.*`
- `12-deployment-request.*`
- `deployment/result.json`
- `13-deployment-summary.md`
- deployment Codex logs and command/runtime evidence,
- public URL(s),
- deployment smoke evidence,
- deployment status in graph state.

### Topology Contract

There is no platform-owned enumeration of supported deployment topologies.
Deployment Codex owns topology discovery and records its reasoning in
`deployment/result.json` and `11-deployment-plan.*`.

Required result fields:

```json
{
  "status": "deployed | blocked | failed",
  "topology_summary": "what Deployment Codex inferred and why",
  "deployment_targets": [],
  "public_urls": [],
  "smoke_targets": [],
  "actions_performed": [],
  "blockers": [],
  "risks": []
}
```

Deployment should block unsupported, unsafe, under-specified, or credential
blocked topologies with an actionable reason.

### Supported Multi-Service Dev Shape

Expected first Azure shape for the current sample, when project evidence proves
it:

```text
Container App: API
Container App: Web
Container Registry: shared dev ACR
Container Apps Environment: shared dev environment
Resource Group: shared dev resource group
```

Deployment should reuse stable dev resources and may adapt names when the
inferred topology requires it.

Stable dev resource names for MSD-001:

```text
resource group: rg-agentic-dev
environment: agentic-dev-env
api app: app-agentic-api-dev
web app: app-agentic-web-dev
registry: agenticdevacr
api image: agenticdevacr.azurecr.io/agentic-api:latest
web image: agenticdevacr.azurecr.io/agentic-web:latest
```

### Redeploy / Nth Release Policy

Deployment Codex must assume the target dev environment may already contain a
previous release. Before creating resources, it should inspect existing Azure and
Docker state and decide whether each resource should be reused, updated,
created, skipped, or blocked.

Default behavior:

- reuse existing dev resource group, registry, Container Apps environment, and
  matching Container Apps when they exist;
- update images, revisions, environment variables, ingress, and service-to-service
  wiring instead of creating duplicate resources;
- create resources only when no compatible stable dev resource exists;
- explain incompatible existing resources instead of silently creating a new
  parallel stack;
- record resource actions in the deployment result and summary.

Deployment result should include:

```json
{
  "resource_changes": [
    {
      "name": "app-agentic-web-dev",
      "type": "container_app",
      "action": "reused | updated | created | skipped",
      "reason": "why this action was chosen"
    }
  ]
}
```

### Deployment Acceptance

- Deployment does not run unless QA passed.
- Deployment summary names created/reused resources.
- Public URL(s) are persisted to delivery state.
- Deployment smoke evidence is persisted without claiming a separate QA Agent
  post-deploy pass.
- Deployment retries only deployment-owned failures; application behavior
  failures route back to Fullstack through QA evidence.
- Logs redact registry passwords, API keys, and secrets.

## Handoff Agent

### Responsibility

Handoff translates technical delivery evidence into a useful stakeholder report.
It is a Codex-owned specialist agent. The platform must not render the report
from fixed business rules; it should only invoke the Handoff Agent and validate
the output contract.

For MSD-001, handoff should be feature-aware and evidence-aware.

### Inputs

- requirements,
- feature queue,
- execution summary,
- implementation QA report/results/strategy,
- deployment smoke result,
- deployment summary,
- public URL(s),
- screenshots/transcripts,
- known blockers.

### Outputs

- `09-handoff-summary.md`
- `handoff/release-report.html`
- `handoff/release-evidence.json`

If sandbox write policy blocks the run-level paths, Handoff may write the same
package under `generated-project/handoff/`; the platform recovers those
contract artifacts.

### Report Sections

Required sections:

```text
Status
Project
Delivered Features
Public URL(s)
How To Use
QA Summary
Deployment Summary
Evidence
Known Limitations
Recommended Next Steps
```

These sections are a minimum stakeholder contract, not a hardcoded template.
The Handoff Agent may add sections, screenshots, links, tables, diagrams, usage
instructions, or references when they make the report more useful.

### Handoff Acceptance

- A business user can understand what was delivered.
- An engineer can find the evidence.
- The public URL is prominent when deployment succeeds.
- Blockers are explicit when implementation QA, deployment, or deployment smoke
  fails.
- Screenshots/transcripts are linked, not pasted.

## Console

### Responsibility

Console shows the run to the user.

For MSD-001, console should make the workflow understandable:

- active feature,
- active agent,
- current stage,
- QA status,
- deployment smoke status,
- repair attempt count,
- deployment topology,
- public URL(s),
- handoff report.

### Console Acceptance

- User can see that the run contains multiple features.
- User can see whether QA is checking API, web, Docker, or browser.
- User can see whether deployment is single-service or API/web.
- User can open the final handoff and deployment summary easily.
