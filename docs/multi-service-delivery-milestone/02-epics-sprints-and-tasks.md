# Epics, Sprints, And Tasks

This document turns MSD-001 into implementable work.

The structure is intentionally project-manager friendly:

- epics define large capability areas,
- sprints define delivery slices,
- tasks define concrete implementation work,
- acceptance criteria define when the task is done.

## Epic Overview

| Epic | Name | Outcome |
| --- | --- | --- |
| E1 | Feature Queue Planning | Requirements become feature work items with acceptance criteria. |
| E2 | Multi-Service Fullstack Generation | Fullstack Agent generates stable API + web projects. |
| E3 | Agentic QA Strategy And Repair | QA derives checks from requirements/topology and drives repair. |
| E4 | Autonomous Deployment And Deployment Smoke | Deployment Codex infers topology from project evidence and publishes stable dev releases. |
| E5 | Evidence-Based Handoff | Handoff Agent writes a professional delivery report. |
| E6 | Console And Run Visibility | Users can understand feature/stage/agent progress. |
| E7 | Architecture And Test Hardening | New contracts are tested and kept inside correct ownership boundaries. |

## Sprint Plan

### Sprint 1: Target Archetype And Planning Contracts

Goal: teach the platform to represent a multi-feature, multi-service target
without changing every agent at once.

Tasks:

#### MSD-001-001: Add milestone sample requirements

Owner: Planning Agent / docs

Create a new example requirement file for the acceptance scenario.

Suggested path:

```text
examples/requirements/multi-service-task-tracker.md
```

Acceptance:

- Includes two simple features.
- Requires API + web.
- States Docker Compose support.
- States Azure dev deployment as expected after QA.
- Keeps product behavior simple enough for fast iteration.

#### MSD-001-002: Extend planning models with feature work items

Owner: Planning Agent

Add a first version of a feature item contract.

Suggested fields:

```text
id
title
user_value
acceptance_criteria
dependencies
suggested_owner_agent
delivery_order
test_notes
deployment_notes
```

Acceptance:

- Planning artifacts can include a `feature_queue`.
- Existing simple chat runs still work.
- Unit tests cover serialization and default behavior.

#### MSD-001-003: Update Planning Agent output

Owner: Planning Agent

Planning should produce:

- project classification,
- staffing decision,
- workflow plan,
- feature queue,
- implementation brief that references feature IDs.

Acceptance:

- The implementation brief clearly tells Fullstack what feature batch to build.
- The workflow plan can represent more than one feature.
- Existing artifacts remain readable.
- During Sprint 1, `api-web-compose` planning output intentionally pauses
  downstream Fullstack, QA, and Deployment until their multi-service slices are
  implemented.

#### MSD-001-004: Add planning schema updates

Owner: Planning Agent

Update Planning Agent JSON Schemas under:

```text
src/agentic_company/agents/planning/schemas/
```

Acceptance:

- Feature queue schema exists or is embedded in workflow plan schema.
- Tests validate a generated planning artifact against the schema.
- No root-level `schemas/` folder returns.

### Sprint 2: Fullstack Feature Iteration And API + Web Conventions

Goal: make Fullstack consume the feature queue one feature at a time while
producing a stable multi-service project without random service names, random
image names, or unnecessary containers.

This sprint intentionally keeps QA, Deployment, and Handoff paused after
Fullstack. The important proof is sequencing: Planning creates `F1` and `F2`,
then Fullstack receives `F1` as one Codex mission and `F2` as the next Codex
mission. Fullstack must not receive both features as one vague batch.

Tasks:

#### MSD-001-005: Add feature-scoped Fullstack execution requests

Owner: Orchestration / Fullstack Agent

Extend delivery state and execution request artifacts so the company graph can
track:

- `feature_queue`,
- `active_feature`,
- `active_feature_id`,
- `completed_feature_ids`,
- `project_archetype`.

Acceptance:

- Planning stores the feature queue in delivery state.
- Planning selects `F1` as the first active feature.
- Fullstack writes a feature-scoped `06-execution-request.json` before each
  Codex run.
- `F1` and `F2` are passed to Codex in delivery order, not together.
- A failed feature stops the iteration and records the active feature as the
  blocker.

#### MSD-001-006: Define generated project convention

Owner: Fullstack Agent / Architect

Document and encode the expected layout.

Required convention:

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

Naming convention:

```text
compose project: agentic-{app-slug}
api service: api
web service: web
api image: agentic-{app-slug}-api:latest
web image: agentic-{app-slug}-web:latest
api container: agentic-{app-slug}-api
web container: agentic-{app-slug}-web
```

`app-slug` is derived from the generated project name: lowercase, non-alphanumeric
characters replaced by hyphens, repeated hyphens collapsed, generic words such
as `multi`, `service`, `web`, `app`, and `mvp` removed, then capped at 24
characters.

Acceptance:

- Fullstack prompt enforces these names for multi-service projects.
- Docker Compose does not generate per-run image names.
- Generated project remains easy to inspect.

#### MSD-001-007: Harden Fullstack prompt policy

Owner: Fullstack Agent

Update Fullstack prompt rules:

- implement only the active feature for the current Codex run,
- preserve already completed feature behavior,
- preserve `.env` if present,
- do not invent extra services,
- keep functionality simple,
- use stable Docker names,
- use `uv.lock`,
- avoid baking secrets into images,
- use API service URL from environment/config,
- produce clear README and execution summary,
- include minimal test hooks where useful.

Acceptance:

- Prompt clearly distinguishes single-service and multi-service patterns.
- Prompt references the active feature ID and its acceptance criteria.
- Prompt lists completed features that must be preserved.
- Prompt tells Codex to avoid random names and redundant artifacts.

#### MSD-001-008: Add generated project inventory checks

Owner: QA Agent / Fullstack Agent

Add checks that validate the expected API + web project shape.

Acceptance:

- QA can detect missing `api/`, `web/`, `Dockerfile.api`,
  `Dockerfile.web`, and Compose services.
- Check only applies when planning says the project is multi-service.
- Single-service chat app checks still pass.

### Sprint 3: Codex-Owned QA Agent And Repair Loop

Goal: QA becomes a Codex-operated quality specialist. The platform invokes QA
Codex for the active feature, but does not choose, predefine, or execute a fixed
QA checklist on the agent's behalf.

Current implementation slice:

- Fullstack now implements only the active feature and waits for QA to accept it.
- The console execution graph loops `Fullstack -> QA -> Fullstack -> QA` for
  API + web feature queues until every feature passes QA or a blocker is hit.
- QA now has a separate Codex identity with agent id `qa-codex-agent`.
- QA Codex owns the full QA job: inspect requirements and implementation,
  decide what evidence is needed, generate helper scripts if useful, execute QA,
  write results, and produce the verdict.
- The platform no longer runs predefined QA checks such as dependency sync,
  compile, Docker config, API scripts, or browser scripts. If those checks are
  appropriate, QA Codex must decide to run them.
- The platform only invokes QA Codex, captures logs/events, validates the output
  contract, and routes pass/fail in the delivery graph.
- QA writes feature-scoped evidence: `qa/results-Fx.json`,
  `08-qa-report-Fx.md`, Codex attempt logs under `qa/codex/Fx/*`, and optional
  QA-created helper/evidence files under `qa/`.
- Failed feature QA writes `10-fix-request-Fx.md/json`, leaves the same active
  feature selected, and routes back to Fullstack repair until the max repair
  budget of 3 is exhausted.
- Deployment and Handoff remain paused after the feature queue passes QA; Sprint
  4 owns topology-aware deployment and deployment smoke validation.

Tasks:

#### MSD-001-009: Add Codex-owned QA execution contract

Owner: QA Agent

Replace platform-selected QA checks with a QA Codex execution contract. QA Codex
must decide and run all checks itself.

Required artifacts:

```text
qa/results-Fx.json
08-qa-report-Fx.md
qa/codex/Fx/attempt-N/*
```

If QA fails:

```text
10-fix-request-Fx.md
10-fix-request-Fx.json
```

Acceptance:

- The platform prompt does not prescribe concrete QA commands.
- The platform does not run predefined QA checks.
- QA Codex final message includes `QA_STATUS: passed` or `QA_STATUS: failed`.
- QA results JSON includes performed checks, acceptance criteria coverage, and
  risks.
- Missing status/artifacts causes a platform contract failure, not a fake pass.

#### MSD-001-010: Let QA Codex choose API/runtime evidence

Owner: QA Agent

For API + web projects, QA Codex should infer what runtime evidence is needed
from the active feature and implementation. The platform must not hardcode
endpoint names or test scripts.

Acceptance:

- QA results explain which runtime/API evidence was gathered.
- For different feature domains, QA Codex can choose different checks without
  platform code changes.
- Failures include evidence paths and suggested repair focus.

#### MSD-001-011: Let QA Codex choose UI/browser evidence

Owner: QA Agent

QA Codex should choose whether browser/UI evidence is needed and, if so, create
and execute the required browser checks itself.

Acceptance:

- Docker runtime E2E starts both services.
- Browser test exercises web UI and confirms API-backed behavior.
- Screenshots and transcripts are captured.

#### MSD-001-012: Add QA repair routing slice

Owner: Orchestration / Fullstack Agent / QA Agent

Ensure failed QA can route back to Fullstack repair.

Acceptance:

- `repair_attempts` max stays 3.
- QA writes focused fix request.
- Fullstack receives failed checks, evidence paths, and feature IDs.
- QA reruns after repair.
- Tests cover pass-after-repair and blocked-after-3-failures.

### Sprint 4: Autonomous Deployment Agent And Deployment Smoke

Goal: Deployment Agent becomes a Codex-owned specialist that understands the
generated project's topology from project evidence instead of platform
hardcoding. It should inspect Docker Compose, Dockerfiles, source layout,
environment examples, QA evidence, and README instructions, then deploy, run
deployment-owned smoke validation, or block with a precise reason.

Deployment is release-batch based by default. Fullstack and QA work
feature-by-feature until the planned feature queue is implementation-QA green;
Deployment then publishes the release candidate once. Per-feature deployment
remains a future strategy that Planning/Deployment can choose explicitly for
high-risk features, hotfixes, or preview environments.

True QA-owned post-deployment validation and post-deploy repair routing are
deferred to the next AgentExecutor/inter-agent-communication milestone.

Tasks:

#### MSD-001-013: Make deployment topology Codex-owned

Owner: Deployment Agent

Deployment Codex should inspect generated artifacts and infer topology without a
predefined platform list of service shapes. Docker Compose is the primary
evidence source when present, but the agent may also use Dockerfiles, README,
source entrypoints, exposed ports, health endpoints, environment examples, and
QA artifacts.

Acceptance:

- The platform does not contain hardcoded topology branches such as "api/web"
  vs "single Streamlit" for deployment execution.
- Deployment Codex writes `deployment/result.json`,
  `11-deployment-plan.*`, `12-deployment-request.*`, and
  `13-deployment-summary.md`.
- Deployment Codex records the inferred topology, evidence, deployment targets,
  blockers, risks, and deployment smoke targets.
- Unsupported or unsafe topology blocks deployment with a useful explanation.
- Tests verify the Codex contract and that the graph contains no concrete
  command/topology nodes.

#### MSD-001-014: Add stable dev resource naming policy

Owner: Deployment Agent / integrations

Define stable dev naming guidance for repeatable autonomous deploys. This is
guidance for Deployment Codex, not a platform command generator.

Example:

```text
resource group: rg-agentic-dev
environment: agentic-dev-env
api app: app-agentic-api-dev
web app: app-agentic-web-dev
registry: agenticdevacr
api image: agenticdevacr.azurecr.io/agentic-api:latest
web image: agenticdevacr.azurecr.io/agentic-web:latest
```

Acceptance:

- Repeat deployments update existing resources.
- Deployment summary explains reused vs created resources.
- No per-run Azure resource names in dev mode.
- Deployment Codex treats every deployment as a possible 2nd, 3rd, or Nth
  release: discover existing resources first, reuse/update by default, create
  only when missing or incompatible.
- Deployment happens once per release batch unless the plan explicitly chooses
  `per_feature`, `hotfix`, or another deployment strategy.
- Deployment Codex may adapt names when the inferred topology requires it, but
  must keep names short and explain deviations.

#### MSD-001-015: Deploy supported API + web shape

Owner: Deployment Agent

Deployment Codex chooses the safest Azure shape for the inferred generated
project. For the current multi-service sample, separate API and web Container
Apps are expected if Docker Compose proves that topology. For future projects,
Deployment Codex must reason from project evidence instead of failing because a
shape was not known ahead of time.

Acceptance:

- Deployment summary lists both apps and URLs.
- Web app is configured with API base URL.
- Deployment Codex runs smoke validation after Deployment reports deployed.
- Deployment Codex records public runtime evidence and any limitations.
- Secrets and env vars are redacted in logs.

#### MSD-001-016: Defer post-deployment QA mode

Owner: QA Agent

Quality Agent should eventually support a post-deployment validation mode that
consumes deployment output and validates the public runtime. This is deferred
from MSD-001 closure.

Acceptance:

- MSD-001 docs explicitly defer true QA-owned post-deployment validation.
- Deployment smoke evidence is not labelled as a QA Agent pass.
- The next milestone keeps this as a first-class AgentExecutor/inter-agent task.

#### MSD-001-017: Defer post-deploy failure routing

Owner: Orchestration / QA Agent / Deployment Agent / Fullstack Agent

Post-deploy failures should eventually route to the right owner. This is
deferred with QA-owned post-deployment validation.

Future routing:

```text
application behavior failure
  -> Fullstack repair
  -> implementation QA
  -> deployment update
  -> post-deploy QA

deployment/configuration failure
  -> Deployment repair/retry
  -> post-deploy QA
```

Acceptance:

- MSD-001 does not claim this loop is complete.
- The current delivery graph stops at deployment smoke plus handoff.
- The next architecture milestone can implement the owner-classification loop.

### Sprint 5: Evidence-Based Handoff

Goal: Handoff becomes a business-quality delivery package.

Implementation direction:

- Handoff is a Codex-owned specialist agent, not a deterministic report
  renderer.
- The platform invokes the Handoff Agent, captures its logs, and validates a
  minimal output contract.
- The Handoff Agent decides report structure, wording, evidence selection, and
  client-facing explanation from the actual planning, Fullstack, QA, deployment,
  and graph-state artifacts.
- The platform may require stable artifact paths, but it should not hardcode the
  report content.

Tasks:

#### MSD-001-018: Add feature delivery report structure

Owner: Handoff Agent

Handoff should include:

- project name,
- delivered feature IDs,
- what changed,
- public URL(s),
- how to use,
- QA evidence summary,
- deployment evidence summary,
- screenshots/transcript links,
- known limitations,
- follow-up recommendations.

Acceptance:

- `09-handoff-summary.md` is useful to a business user.
- Technical evidence is linked but not dumped into the main prose.
- If deployment is blocked, handoff clearly says why and what is needed.

#### MSD-001-019: Add release-level summary

Owner: Handoff Agent

If multiple features are delivered in one run, produce one release summary.

Suggested artifact:

```text
09-handoff-summary.md
handoff/release-report.html
handoff/release-evidence.json
```

Acceptance:

- Each feature has status: delivered, blocked, repaired, or deferred.
- QA/deployment status is visible per feature or release batch.
- Public URLs are copied from graph state/deployment summary.

#### MSD-001-020: Prepare for future PDF export

Owner: Handoff Agent / Console

Do not implement PDF first if it slows the milestone. Prepare the report so PDF
export is easy later.

Acceptance:

- Handoff markdown has stable sections.
- Print-friendly HTML exists so a user can save the report as PDF from a
  browser.
- Evidence paths are structured.
- Screenshots are referenced with relative paths.
- Future exporter can turn the report into PDF without scraping random logs.

### Sprint 6: Console Visibility And Hardening

Goal: make the milestone understandable while it runs.

This sprint prepares the console for demo and video-recording use. The console
should not assume a two-feature toy run; it should remain readable when a future
milestone produces 5-10 feature work items in one release batch. The main screen
should answer:

- where the run is now,
- which features are done, active, pending, repaired, or blocked,
- what topology was generated/deployed,
- where the public app can be opened,
- where the final client report lives,
- where technical evidence can be inspected when needed.
- how to start the automatic delivery workflow without stale manual controls
  from older architecture slices.

True QA-owned post-deployment validation is intentionally deferred to the next
AgentExecutor/inter-agent-communication milestone. In MSD-001, Deployment owns
deployment smoke evidence, and the console should present that honestly without
claiming it is a separate QA Agent post-deploy pass.

Tasks:

#### MSD-001-021: Show feature queue in console

Owner: Console

Acceptance:

- Console shows feature IDs, titles, status, and current agent/stage.
- Feature status updates from graph state or artifacts.
- Feature queue remains compact for 5-10 features.
- Repair attempt counts are visible without opening raw state JSON.
- The active feature is visually distinguishable during a run.
- The run view defaults to an overview instead of raw artifacts.
- User-facing labels render `QA` consistently, never `Qa`.

#### MSD-001-022: Show topology and deployment targets

Owner: Console / Deployment Agent

Acceptance:

- Console shows whether the project is single-service or API/web.
- Deployment target names are visible before/after deployment.
- Public URLs are shown clearly after deployment.
- The primary app link is easy to open during demo.
- Deployment smoke evidence is labelled as deployment-owned until true
  QA-owned post-deploy validation is introduced.
- Manual deployment confirmation controls are removed from the console because
  deployment now runs as part of the automatic delivery graph.

#### MSD-001-023: Add milestone regression suite

Owner: Tests / all agents

Acceptance:

- Unit tests cover new planning models and schemas.
- Unit tests cover QA strategy mapping.
- Unit tests cover topology detection.
- Unit tests cover handoff report sections.
- At least one integration test exercises the sample requirement through
  planning artifacts without live Codex/Azure.
- Console overview tests cover more than two features to guard future release
  batch scalability.
- Console label tests cover `QA` capitalization for graph/status tokens.

## Suggested Branch Sequence

Use feature/epic/task naming:

```text
feature/multi-service-delivery/planning-feature-queue
feature/multi-service-delivery/fullstack-conventions
feature/multi-service-delivery/qa-strategy
feature/multi-service-delivery/repair-loop
feature/multi-service-delivery/deployment-topology
feature/multi-service-delivery/handoff-report
feature/multi-service-delivery/console-visibility
```

Each branch should be mergeable on its own.

## Milestone Exit Criteria

The milestone can be called complete when the sample multi-service task tracker
can run through:

```text
planning
  -> fullstack
  -> qa
  -> repair if needed
  -> deployment
  -> handoff
```

and the handoff report is good enough to send to a non-engineering stakeholder.
