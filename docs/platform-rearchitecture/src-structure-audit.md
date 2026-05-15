# Source Structure Audit

Date: 2026-04-27

This audit is the Stage 6 cleanup contract for `src/agentic_company`.
The goal is not only to list files. The goal is to make the architecture honest:
the top-level LangGraph invokes specialist agents in a predefined order, every
specialist owns its own capability modules, and reusable tool code lives in
shared platform or integration packages.

## Target Shape

The source tree should converge toward this shape:

```text
src/agentic_company/
  platform/
    state.py
    artifacts.py
    events.py
    security.py
    models.py

  integrations/
    codex/
      cli.py
      events.py
      prompts.py
    azure/
      container_apps.py
    docker/
      compose.py
      images.py
    playwright/
      browser.py
    commands.py

  orchestration/
    graphs/
      delivery.py
      routing.py
      rendering.py
    runtime.py
    stages.py

  agents/
    planning/
      agent.py
      graph.py
      nodes.py
      models.py
      schemas/
    fullstack/
      agent.py
      graph.py
      nodes.py
    quality/
      agent.py
      graph.py
      checks/
      models.py
    deployment/
      agent.py
      graph.py
      nodes.py
      models.py
    handoff/
      agent.py
      graph.py
      summary.py

  console/
    streamlit_app.py
    views/
    services/
```

The current code is mid-migration. Anything that does not match this target
must be explicitly marked as either **temporary compatibility** or **shared
infrastructure**.

Important scope rule: Stage 6 should optimize ownership boundaries, not
over-polish deterministic internals that real agents will replace. For example,
the current deployment runner is useful as a tool backend, but the future
Deployment Agent should reason, ask for missing inputs, choose or revise a
deployment strategy, and call integrations as tools. We should avoid spending
too much time making the old linear deployment implementation elegant if that
work does not move us toward the real agent-of-agents platform.

## Decision Legend

- **Keep**: the file has a clear current and future responsibility.
- **Keep, split later**: the responsibility is valid, but the file is too large
  or will naturally split as the agent grows.
- **Move now/soon**: the responsibility belongs elsewhere and should be moved
  during Stage 6 cleanup.
- **Temporary compatibility**: the file exists only to avoid breaking callers
  during migration.
- **Generated artifact**: checked in or generated because it supports the
  workflow, not runtime logic.

`__pycache__` folders are local runtime cache directories and are not source.

## Top-Level Package

### `src/agentic_company/__init__.py`

Purpose: marks the package import root.

Why it exists: Python needs a package boundary so imports remain stable.

Can we remove it? No. Without it, imports such as
`agentic_company.console.app` become fragile.

Decision: **Keep**. Rename the package only in a dedicated package-rename PR.

### Removed: `src/agentic_company/models.py`

Status: removed from active architecture.

Why: it mixed concepts that now have clearer owners. Planning artifacts belong
to the Planning Agent, QA artifacts belong to the QA Agent, deployment artifacts
belong to the Deployment Agent, and only truly cross-agent contracts belong at
platform level.

Current split:

- `agents/planning/models.py`: intake brief, project classification, staffing
  decision, workflow phase, workflow plan.
- `platform/models.py`: cross-agent execution request and agent run result.
- `agents/quality/models.py`: QA result/test-plan models.

Decision: **Keep removed**. Do not add new models to the package root.

### Removed: `src/agentic_company/logging_config.py`

Status: moved to `platform/logging.py`.

Why: logging configuration is platform/process setup. It is not an integration
because it does not talk to an external tool or provider.

Decision: **Keep removed**. Import `configure_logging` from
`agentic_company.platform.logging`.

## Platform Layer

### `src/agentic_company/platform/state.py`

Purpose: defines `DeliveryState`, initial state creation, and state mutation
helpers.

Why it exists: LangGraph needs a common state schema that is not owned by any
one specialist.

Can we remove it? No. Collapsing it into orchestration would make agents depend
on orchestration internals; collapsing it into one agent would make that agent
too powerful.

Decision: **Keep**.

### `src/agentic_company/platform/artifacts.py`

Purpose: defines shared artifact references and artifact kinds.

Why it exists: artifacts are cross-agent outputs, so they belong at platform
level.

Can we remove it? No. Without a shared artifact reference, each agent would
invent its own metadata.

Decision: **Keep**.

### `src/agentic_company/platform/models.py`

Purpose: shared platform contracts used across specialist agents.

Current contents:

- `ExecutionRequest`
- `AgentRunResult`

Why it exists: these contracts cross agent boundaries. Planning writes an
execution request, Fullstack consumes it, QA and Deployment inspect it, and
multiple agents return run results.

Decision: **Keep**. Move a model out only when it becomes clearly owned by one
specialist.

### `src/agentic_company/platform/logging.py`

Purpose: process-wide logging setup for console and CLI entry points.

Why it exists: avoids each entry point configuring logging differently and keeps
Streamlit reruns from duplicating handlers.

Why not `integrations/`: logging config is not an external tool integration.
It belongs to platform setup.

Decision: **Keep**.

### `src/agentic_company/platform/events.py`

Purpose: shared event writing for `events.jsonl`.

Why it exists: events are platform-level telemetry. They are used by
orchestration, console, QA, deployment, handoff, and future agents.

Decision: **Keep**.

### `src/agentic_company/platform/security.py`

Purpose: shared redaction helpers for logs, command output, console views, and
reports.

Why it exists: redaction is not QA-specific. Fullstack logs, deployment logs,
console live logs, Docker Compose output, Azure CLI output, and handoff
summaries all need the same security boundary.

Decision: **Keep**. QA imports the shared redactor instead of owning it.

## Integrations Layer

The integrations layer is currently missing as a package. That is the biggest
remaining structural gap.

### `src/agentic_company/integrations/commands.py`

Purpose: shared subprocess streaming and command-log helpers.

Why it exists: command streaming is not a runner concept and not owned by any
one agent. Fullstack, QA, and Deployment all need it.

Decision: **Keep**.

### `src/agentic_company/integrations/codex/`

Purpose: reusable Codex CLI discovery and event normalization helpers.

Why it exists: Codex is a tool integration, not the identity of the Fullstack
Agent. Today the Fullstack Agent uses Codex, but later other agents may use
Codex for review, repair, migration, or codebase analysis.

Decision: **Keep, continue splitting**.

Current split:

- `integrations/codex/cli.py`: Codex binary discovery.
- `integrations/codex/events.py`: raw Codex event parsing, redaction, and
  normalization.

Remaining target split:

- `integrations/codex/runner.py`: generic process execution and command
  construction.
- `integrations/codex/transcript.py`: transcript/log extraction if event
  handling grows.
- `agents/fullstack/prompts.py`: fullstack-specific prompt wording should stay
  agent-owned.
- `agents/fullstack/graph.py`: decides when to call the Codex integration.

The Fullstack Agent should own intent. The Codex integration should own tool
mechanics.

### Missing: `src/agentic_company/integrations/azure/`

Current code: reusable Azure Container Apps command builders live in
`integrations/azure/container_apps.py`; command execution and deployment
summary assembly still live in the Deployment Agent backend.

Problem: Azure command mechanics are reusable by future deployment,
observability, teardown, and cost-control agents.

Decision: **Keep, expand when useful**. Do not prematurely move every
deployment detail out of the agent, but new reusable Azure CLI mechanics should
go here.

### `src/agentic_company/integrations/docker/`

Current code: reusable Docker image command builders live in
`integrations/docker/images.py`.

Problem: Docker build, compose, image naming, and cleanup are tool mechanics.
QA and Deployment should call shared Docker integration helpers.

Decision: **Keep, expand when another agent needs the same Docker behavior**.

## Agents Layer

### `src/agentic_company/agents/base.py`

Purpose: shared agent descriptor, delivery-agent protocol, and common
state/artifact helpers.

Why it exists: every specialist needs a consistent descriptor and state-handling
convention.

Decision: **Keep**.

### `src/agentic_company/agents/registry.py`

Purpose: static registry of active specialist agents.

Why it exists: the console, docs, graph runtime, and future platform UI need a
discoverable list of active specialists.

Decision: **Keep, split later** if registry grows into dynamic provider/config
loading.

## Planning Agent

### `src/agentic_company/agents/planning/agent.py`

Purpose: first-class Planning Agent wrapper.

Why it exists: the company graph must invoke planning as a specialist, not as a
loose pipeline function.

Decision: **Keep**.

### `src/agentic_company/agents/planning/graph.py`

Purpose: Planning Agent subgraph.

Why it exists: planning is now structurally consistent with Fullstack, QA,
Deployment, and Handoff.

Decision: **Keep, expand soon**.

### `src/agentic_company/agents/planning/schemas/`

Purpose: JSON Schema contracts for Planning Agent artifacts:

- intake brief
- project classification
- staffing decision
- workflow plan

Why it exists here: these schemas describe Planning Agent outputs. They are not
generic platform contracts and should not live in a root-level catch-all
`schemas/` folder.

Decision: **Keep** with Planning. If a schema becomes a true cross-agent
contract, promote it to `platform/` with an explicit owner.

### Removed: `src/agentic_company/pipeline/`

Status: removed from active architecture.

Why: top-level pipeline was historical architecture. The high-level pipeline is
now the delivery LangGraph. Detailed planning behavior belongs inside the
Planning Agent.

Current mapping:

- `pipeline/intake.py` -> `agents/planning/intake.py`
- `pipeline/classification.py` -> `agents/planning/classification.py`
- `pipeline/team_assembly.py` -> `agents/planning/team_assembly.py`
- `pipeline/workflow_planning.py` -> `agents/planning/workflow_planning.py`
- `pipeline/run.py` -> `agents/planning/run.py`

Decision: **Keep removed**. Do not add new code under `pipeline/`.

## Fullstack Agent

### `src/agentic_company/agents/fullstack/agent.py`

Purpose: first-class Fullstack Agent wrapper used by the company graph.

Why it exists: the company graph should invoke an implementation specialist,
not a raw Codex runner.

Decision: **Keep**.

### `src/agentic_company/agents/fullstack/graph.py`

Purpose: Fullstack Agent subgraph: prepare context, run implementation backend,
apply result.

Why it exists: it turns implementation into a graph-backed specialist instead
of a direct tool call.

Decision: **Keep, split later** when repair planning, multi-provider execution,
or implementation review become separate nodes.

### `src/agentic_company/agents/fullstack/codex_cli.py`

Purpose today: Codex-backed implementation runner used by the Fullstack Agent.

Current problem: this file mixes fullstack intent with reusable Codex tool
mechanics.

Decision: **Keep, continue shrinking**. Generic Codex CLI discovery, command
construction, process streaming, event parsing, and event normalization now live
under `integrations/codex/`. This module should keep only Fullstack Agent
intent: prompt construction, execution policy, and summary rendering.

What remains in Fullstack:

- fullstack prompt construction,
- implementation policy,
- repair input shaping,
- graph node decisions.

What moved to integration:

- Codex process execution,
- Codex event parsing,
- command transcript normalization,
- low-level status parsing.

## Quality Agent / QA Specialist

### `src/agentic_company/agents/quality/agent.py`

Purpose: first-class Quality Agent wrapper for the QA specialist role used by the company graph.

Decision: **Keep**.

### `src/agentic_company/agents/quality/graph.py`

Purpose: Quality Agent LangGraph: prepare context, collect evidence, plan checks,
run static/Python/Docker/browser checks, summarize, write report, apply result.

Decision: **Keep**. This is the best current example of the desired
agent-subgraph architecture.

### `src/agentic_company/agents/quality/runner.py`

Purpose: compatibility wrapper around the QA graph for code/tests that still
expect a runner-like object.

Decision: **Temporary compatibility**. Delete once all callers use `QualityAgent` or
the QA graph directly.

### QA check modules

Current files:

- `models.py`
- `plan.py`
- `reports.py`
- `fix_request.py`
- `files.py`
- `static_checks.py`
- `commands.py`
- `python_checks.py`
- `docker_checks.py`
- `docker_summary.py`
- `playwright_checks.py`

Decision: **Keep**. These are real QA-owned capabilities. Later, the check files
can be grouped under `agents/quality/checks/`, but do not collapse them into
`graph.py`.

## Deployment Agent

### `src/agentic_company/agents/deployment/agent.py`

Purpose: first-class Deployment Agent wrapper used by the company graph.

Decision: **Keep**.

### `src/agentic_company/agents/deployment/graph.py`

Purpose: Deployment Agent graph: prepare deployment plan/request, validate
environment, run Azure/Docker deployment, read public URL, run post-deploy QA,
write summary, apply result.

Decision: **Keep, evolve into a real agent graph**. It is currently linear and
mostly deterministic. The next meaningful improvement is not just splitting the
file; it is turning deployment into an agent that owns decisions and can use
Azure, Docker, secret, and post-deploy QA tools.

Only split deterministic internals when the split supports real agent behavior,
such as:

- `state.py`
- `nodes.py`
- `tools.py`
- `azure_tools.py`
- `post_deploy_qa.py`

Important naming decision: the company graph should expose one specialist node:
`deployment`. Internal deployment preparation belongs inside the Deployment
Agent graph, not as a separate top-level `deployment_prepare_context` node.

### `src/agentic_company/agents/deployment/planner.py`

Purpose: deterministic Azure Container Apps plan/request generation.

Decision: **Keep** inside Deployment Agent.

### `src/agentic_company/agents/deployment/runner.py`

Purpose today: low-level Azure/Docker command executor and deployment summary
renderer.

Current problem: this file mixes Deployment Agent decisions with reusable Azure
and Docker integration mechanics.

Decision: **Temporary tool backend**. Keep it working, but do not over-optimize
it as the final architecture. The real destination is a Deployment Agent that
calls Azure/Docker integrations as tools and can route through missing
credentials, failed provisioning, post-deploy QA failure, teardown, or retry.

Target split:

- `integrations/azure/container_apps.py`
- `integrations/docker/images.py`
- `agents/deployment/summary.py`
- `agents/deployment/nodes.py`

Priority: extract reusable tool mechanics only when another agent or the real
Deployment Agent graph needs them. Do not create many tiny deployment files just
to make the current deterministic runner look neat.

## Handoff Agent

### `src/agentic_company/agents/handoff/agent.py`

Purpose: first-class Handoff Agent wrapper used by the company graph.

Decision: **Keep**.

### `src/agentic_company/agents/handoff/graph.py`

Purpose: Handoff Agent subgraph: prepare context, write summary, apply result.

Decision: **Keep**. Handoff must remain a specialist like every other stage.

### `src/agentic_company/agents/handoff/summary.py`

Purpose: renders and writes the final handoff summary.

Decision: **Keep**.

## Orchestration Layer

### `src/agentic_company/orchestration/runtime.py`

Purpose: starts delivery graph runs, persists graph state, and writes graph-node
events.

Why it exists: console should call a graph runtime, not know every specialist
implementation.

Decision: **Keep**.

### `src/agentic_company/orchestration/graphs/delivery.py`

Purpose: builds and runs the top-level company delivery LangGraph.

Decision: **Keep**. This is the graph of specialist agents.

### `src/agentic_company/orchestration/graphs/nodes.py`

Purpose: maps top-level graph node names to specialist agent calls.

Decision: **Keep**. It should remain thin. If business logic appears here, move
that logic into the relevant specialist agent.

### `src/agentic_company/orchestration/graphs/routing.py`

Purpose: defines top-level graph order and future routing points.

Decision: **Keep**. Real conditional routing and repair loops belong here, but
agent internals do not.

### `src/agentic_company/orchestration/graphs/rendering.py`

Purpose: renders the Mermaid delivery graph artifact.

Decision: **Keep**. Maintain exactly one checked-in delivery graph artifact:
`src/agentic_company/orchestration/graphs/delivery-graph.mmd`.

## Console Layer

### `src/agentic_company/console/streamlit_app.py`

Purpose today: Streamlit UI entry point for requirements input, run control, credentials,
artifacts, live logs, timeline, and deployment action.

Current problem: it still owns several rendering areas and can be split further
as the web console grows.

Decision: **Keep, split incrementally**.

Current split:

- `console/streamlit_app.py`: Streamlit entry point.
- `console/views/live_logs.py`: live log rendering.
- `console/services/graph_artifacts.py`: graph artifact refresh.
- `console/app.py`: compatibility wrapper for existing Streamlit commands.

Target remaining split:

- `console/views/`: artifact, timeline, stage, credential, and summary views.
- `console/services/`: run creation, credentials, graph runtime launch, cleanup.

Keep `console/app.py` temporarily as a compatibility entry point while docs and
local habits still point to it.

### `src/agentic_company/console/support.py`

Purpose today: console service layer.

Current problem: it contains several different service responsibilities.

Decision: **Keep, split later** into:

- `runs.py`
- `credentials.py`
- `artifacts.py`
- `threads.py`

### `src/agentic_company/console/live_logs.py`

Purpose: converts workflow events, Codex events, QA command logs, and deployment
command logs into user-friendly live log entries.

Decision: **Keep**. This is console-specific presentation logic, not agent
logic.

## Removed Historical Runner Package

### `src/agentic_company/runners/`

Status: removed from the active architecture.

Why: specialist runners moved into specialist agents, event writing moved to
`platform/events.py`, command streaming moved to `integrations/commands.py`, and
execution-request loading moved to `platform/artifacts.py`.

Decision: **Keep removed**. Do not add new agent logic under `runners/`.

## Current Structural Findings

1. The main specialist shape is now right:
   - Planning Agent
   - Fullstack Agent
   - QA Agent
   - Deployment Agent
   - Handoff Agent

2. The top-level delivery graph should stay simple:
   `planning -> fullstack -> qa -> deployment -> handoff`.

3. Agent internals should be subgraphs or agent-owned modules. The top-level
   graph should not expose implementation details such as deployment context
   preparation.

4. Historical `pipeline/`, `runners/`, root `models.py`, and root
   `logging_config.py` are now removed from the active source layout.

5. `agents/fullstack/codex_cli.py`, `agents/deployment/runner.py`, and
   `console/streamlit_app.py` are still large because they were grown before the final package
   boundaries existed. They should be split, not collapsed.

6. Redaction, event writing, and command streaming now have shared homes under
   `platform/` and `integrations/`.

## Completed Stage 6 Cleanup

This cleanup pass established the current agent-of-agents source shape without
pretending the deterministic internals are the final product.

1. Fullstack/Codex ownership is clearer:
   - `agents/fullstack/` owns Fullstack Agent intent, prompt construction,
     implementation policy, graph state updates, and summary rendering.
   - `integrations/codex/` owns reusable Codex CLI discovery, event parsing,
     normalization, and process execution mechanics.
2. Console ownership is clearer:
   - `console/streamlit_app.py` is the Streamlit entry point.
   - `console/views/live_logs.py` owns live-log presentation.
   - `console/services/graph_artifacts.py` owns Mermaid artifact refresh.
   - `console/app.py` remains only as a compatibility entry point for existing
     commands.
3. Deployment is now a specialist graph:
   - The top-level delivery graph exposes one specialist node: `deployment`.
   - Deployment planning, Azure/Docker execution, post-deploy QA, summary
     writing, and public URL propagation are owned by the Deployment Agent.
   - Reusable Azure and Docker command-building mechanics live under
     `integrations/azure/` and `integrations/docker/`.
4. Tests are grouped by architecture layer:
   - `tests/unit/agents/`
   - `tests/unit/console/`
   - `tests/unit/orchestration/`
   - `tests/unit/integrations/`
   - `tests/integration/`
   - shared fixtures live in `tests/conftest.py`.

## Remaining Structural Follow-Ups

These are the next cleanup targets, but they should be driven by real agent
capability work rather than file-count reduction.

1. Continue splitting `console/streamlit_app.py` into artifact, timeline,
   stage, credential, and summary views as the UI grows.
2. Continue shrinking `agents/fullstack/codex_cli.py` only when the Fullstack
   Agent gains real capabilities such as repair prompts, provider selection,
   implementation strategy, or code review loops.
3. Promote Deployment from a deterministic Azure backend into a real agent that
   can inspect project topology, choose or revise deployment plans, handle
   multi-container projects, request missing credentials, and recover from
   provisioning or post-deploy QA failures.
4. Expand integrations only when multiple agents need the same tool mechanics.
   Shared tools belong under `integrations/`; specialist decisions stay inside
   the relevant agent.

Do not reduce file count as the primary goal. The goal is clean ownership:
specialists own specialist behavior, integrations own external tools, and
platform owns shared contracts/security/events.
