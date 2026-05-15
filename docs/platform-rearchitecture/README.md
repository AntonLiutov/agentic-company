# Platform Rearchitecture

This is the active development stage after the first end-to-end PoC.

The Milan roadmap proved the first vertical slice:

```text
requirements -> planning -> Codex implementation -> QA -> Azure deployment -> post-deploy QA -> handoff
```

That flow works for a simple Streamlit LLM chat generated project, but the code still carries PoC
shape: orchestration decisions are spread across the console, runners, review code, QA, deployment,
and handoff helpers. The next stage is to turn this into the real platform architecture.

## Goal

Build the production orchestration spine now:

```text
Company Delivery Graph
  -> Planning Agent Graph
  -> Fullstack Agent Graph
  -> Quality Agent Graph
  -> Deployment Agent Graph
  -> Handoff Agent Graph
```

Each agent can be a LangGraph graph. The platform-level LangGraph composes those agent graphs into a
delivery workflow. The first graph can run linearly, but it must already have the shape needed for
branches, retries, approvals, and interrupts.

## Why Now

If orchestration remains hidden inside ad hoc runner calls, every new interaction will increase
coupling:

- QA failure repair loop
- deployment approvals
- missing credential prompts
- upstream planning revisions
- downstream deployment failures
- multiple generated project archetypes
- deeper agent internals

The graph architecture should be introduced before those behaviors become large, so future work adds
edges and nodes instead of rewriting the system.

## Architecture Principle

LangGraph owns flow. Agents own work. Integrations own tools. Artifacts own evidence.

```text
console/chat
  -> graph runtime
  -> company delivery graph
  -> agent subgraphs
  -> integrations
  -> artifacts/events/state
```

Avoid putting all business logic directly inside LangGraph node functions. A node should call a
clear service or agent wrapper, update state, and return.

## Initial Linear Graph

The first graph should preserve current behavior:

```text
planning
  -> fullstack
  -> qa
  -> deployment
  -> handoff
```

For the migration period, deployment can be automatic in dev mode when required inputs are already
available. This keeps the PoC runnable while the console is being moved from button-driven runner
calls to graph-driven execution.

## Near-Term Branching Graph

After the linear graph is stable:

```text
planning
  -> fullstack
  -> qa
  -> route_after_qa

route_after_qa:
  passed -> deployment
  failed with retries left -> repair_request -> fullstack_fix -> qa
  failed with no retries left -> blocked

deployment
  -> prepare_context
  -> write_deployment_plan
  -> write_deployment_request
  -> deployment_approval
  -> execute_azure_deployment
  -> post_deploy_qa
  -> route_after_deployment

route_after_deployment:
  passed -> handoff
  failed -> deployment_blocked
```

## Temporary Dev Auto-Confirm Mode

During the rearchitecture, it is acceptable to avoid blocking on console credential/deployment
clicks.

Temporary rule:

- If `generated-project/.env` has all required app variables, treat the credential step as satisfied.
- If local `az account show` succeeds and Docker is available, allow dev deployment to auto-confirm.
- Deployment still uses stable dev resources and writes full evidence.
- This mode is for local development only and must be visible in state/logs as `auto_confirmed_dev`.

Reason:

- The current console controls will be changing.
- We need to keep the full PoC executable while restructuring.
- The later production design will use checkpoints and interrupts for user input.

## Checkpoints And Interrupts Later

The production graph should use a checkpointer and explicit interrupt states.

Interrupt examples:

- `missing_requirements`: ask the user for clarifying input.
- `missing_credentials`: ask for required environment variables.
- `approve_execution`: confirm before Codex edits files.
- `qa_failed`: ask whether to repair automatically or stop.
- `approve_deployment`: confirm Azure account, subscription, target, and cost risk.
- `deployment_failed`: ask whether to retry, inspect logs, or block handoff.

The console or chat UI should resume a checkpointed graph with the user response instead of manually
calling individual runner functions.

## State Model

The graph should have one source of truth:

```text
DeliveryState
  run_id
  run_dir
  target_project_dir
  stage
  status
  project_name
  selected_agents
  artifacts
  events
  qa_status
  deployment_status
  public_url
  repair_attempts
  max_repair_attempts
  approvals
  auto_confirmations
  blockers
```

Files remain durable evidence. State is the routing brain.

## Agent Subgraphs

### Planning Agent Graph

```text
parse_intake
  -> classify_project
  -> assemble_team
  -> plan_workflow
  -> render_implementation_brief
  -> write_execution_request
```

### Fullstack Agent Graph

```text
load_execution_request
  -> prepare_codex_prompt
  -> run_codex
  -> parse_codex_events
  -> write_execution_summary
```

### Quality Agent Graph

```text
build_test_plan
  -> artifact_checks
  -> static_security_checks
  -> python_checks
  -> streamlit_checks
  -> docker_checks
  -> browser_checks
  -> summarize_results
  -> maybe_create_fix_request
```

### Deployment Agent Graph

```text
load_deployment_request
  -> check_azure_account
  -> check_docker
  -> ensure_resource_group
  -> ensure_registry
  -> build_and_push_image
  -> create_or_update_container_app
  -> run_post_deploy_qa
  -> write_deployment_summary
```

### Handoff Agent Graph

```text
collect_delivery_artifacts
  -> verify_deployment_summary
  -> render_handoff
  -> write_handoff
```

## Module Target

Target structure:

```text
src/agentic_company/
  platform/
    state.py
    artifacts.py
    approvals.py
    events.py

  orchestration/
    graphs/
      delivery.py
      state.py
      nodes.py
      routing.py
    runtime.py

  agents/
    base.py
    registry.py
    planning/
    fullstack/
    quality/
    deployment/
    handoff/

  integrations/
    azure/
    codex/
    docker/
    playwright/
    streamlit/

  console/
    views/
    services/
```

Do not start by moving every file. Start with graph wrappers around the current working services,
then move internals module by module.

## Migration Plan

This section is the implementation contract. Future work should proceed stage by stage. Do not start
with broad file movement. Each stage must preserve the current PoC unless the stage explicitly says
otherwise.

### Stage 1: Delivery Graph Shell

Recommended branch:

```text
codex/add-delivery-graph-shell
```

Goal:

- Introduce LangGraph as the orchestration spine while keeping the current runner APIs working.
- Prove that the existing delivery flow can be represented as a graph before moving internals.

Add:

```text
src/agentic_company/platform/
  state.py
  artifacts.py

src/agentic_company/orchestration/graphs/
  delivery.py
  nodes.py
  routing.py

tests/unit/orchestration/test_delivery_graph.py
```

Implementation notes:

- Add the LangGraph dependency.
- Define `DeliveryState`.
- Add graph node wrappers around the current code:

```text
planning_node -> run_pipeline
fullstack_node -> CodexCliRunner
qa_node -> QualityRunner
deployment_node -> DeploymentAgent graph
handoff_node -> write_handoff_summary
```

- The first graph can be linear:

```text
planning -> fullstack -> qa -> deployment -> handoff
```

- One LangGraph Mermaid artifact is checked in for visualization:
  - `src/agentic_company/orchestration/graphs/delivery-graph.mmd`
- `delivery-graph.mmd` is the default expanded delivery graph and includes known agent
  subgraphs inline instead of writing separate `.mmd` files for each agent.
- The Streamlit console refreshes graph Mermaid artifacts on startup via
  `write_graph_artifacts(...)`, so the checked-in diagrams stay aligned with the compiled graphs.

- Existing direct runner entry points must keep working.
- Existing console behavior does not need to move to the graph in this stage.

Acceptance criteria:

- A unit test proves the graph executes nodes in the expected order with injected/fake node
  implementations.
- `DeliveryState` records `run_id`, `run_dir`, `target_project_dir`, `stage`, `status`,
  `qa_status`, `deployment_status`, `artifacts`, `blockers`, and `auto_confirmations`.
- The graph can be invoked in tests without calling live Codex, Docker, Azure, or Playwright.
- Existing tests still pass.

Validation:

```powershell
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev pytest
```

Do not:

- Move QA, deployment, Codex, or console files yet.
- Add the repair loop yet.
- Replace the console execution buttons yet.
- Require Azure credentials in tests.

### Stage 2: Console Uses Graph Runtime

Recommended branch:

```text
codex/run-console-through-delivery-graph
```

Goal:

- Make the console start and observe graph execution instead of manually chaining runner calls.

Add or change:

```text
src/agentic_company/orchestration/runtime.py
src/agentic_company/console/services/
```

Implementation notes:

- Add a graph runtime service with `start(run_dir)` and `resume(run_dir, input)` style methods.
- The console should read graph state, events, artifacts, and logs.
- Keep current console controls available while migrating.
- Use dev auto-confirm mode when all requirements are already satisfied:

```text
credentials auto-confirmed if generated-project/.env has required variables
deployment auto-confirmed if Docker is running and az account show succeeds
```

- Record auto-confirmed steps in state as `auto_confirmed_dev`.

Acceptance criteria:

- The console can run the current PoC path through the graph runtime.
- User-facing state shows graph stage and status.
- Dev auto-confirm is visible, not silent.
- Existing non-graph CLI paths still work.

Validation:

```powershell
uv run --extra dev ruff format --check .
uv run --extra dev ruff check .
uv run --extra dev pytest
```

Do not:

- Remove old runner functions until the graph path is stable.
- Add production checkpointing yet.
- Hide approval/deployment behavior from logs.

### Stage 3: First-Class Agent Wrappers

Recommended branch:

```text
codex/agent-wrappers
```

Goal:

- Convert the current runners into agent-facing wrappers so the company graph composes agents, not
  random helper classes.

Add:

```text
src/agentic_company/agents/base.py
src/agentic_company/agents/registry.py
src/agentic_company/agents/planning/agent.py
src/agentic_company/agents/fullstack/agent.py
src/agentic_company/agents/quality/agent.py
src/agentic_company/agents/deployment/agent.py
src/agentic_company/agents/handoff/agent.py
```

Agent contract:

```python
class Agent(Protocol):
    agent_id: str
    runtime: str

    def run(self, state: DeliveryState) -> DeliveryState:
        ...
```

Implementation notes:

- `PlanningAgent` wraps the deterministic planning pipeline.
- `FullstackAgent` wraps `CodexCliRunner`.
- `QualityAgent` wraps `QualityRunner`.
- `DeploymentAgent` wraps deployment plan/request and `AzureDeploymentRunner`.
- `HandoffAgent` wraps handoff rendering.
- The graph should call agent wrappers, not lower-level helpers directly.

Acceptance criteria:

- Agent registry can list active agents and runtime type.
- The delivery graph composes agent wrappers.
- Current behavior remains unchanged.
- Tests cover wrapper status mapping and artifact output mapping.

Do not:

- Split internal QA/deployment workflows yet.
- Rename all existing packages in one PR.
- Change artifact filenames unless required by state tracking.

### Stage 4: Agent Internal Subgraphs

Recommended branches:

```text
feature/agent-subgraphs/quality-agent-graph
feature/agent-subgraphs/quality-internal-nodes
```

Goal:

- Convert the largest tool agents into internal LangGraph subgraphs while keeping them visible as
  single nodes in the company graph.
- QA should be split into concrete internal nodes once the graph boundary is stable.
- Deployment can start as a compatibility subgraph around the existing Azure runner, then split
  internals in a later deployment-focused slice.

Quality Agent graph:

```text
prepare_context
  -> check_existing_evidence
  -> prepare_evidence
  -> build_test_plan
  -> artifact_checks
  -> static_security_checks
  -> python_checks
  -> docker_checks
  -> browser_checks
  -> summarize_results
  -> write_report
  -> apply_result
```

Deployment Agent graph:

```text
load_deployment_request
  -> check_azure_account
  -> check_docker
  -> ensure_resource_group
  -> ensure_registry
  -> build_and_push_image
  -> create_or_update_container_app
  -> run_post_deploy_qa
  -> write_deployment_summary
```

Implementation notes:

- Start with QA because it has the clearest deterministic check sequence.
- Keep old `QualityRunner` as a compatibility facade until the new graph is stable.
- Then convert deployment with the same pattern.
- Each subgraph should expose one public agent `run(state)` method to the company graph.

Acceptance criteria:

- QA subgraph writes the same QA report/results/log artifacts as today.
- Deployment subgraph writes the same deployment summary/log/handoff behavior as today.
- Existing tests still pass or are updated without weakening coverage.
- New tests prove subgraph node order and pass/fail routing.

Do not:

- Add repair loop inside the QA subgraph before the company graph routing is ready.
- Couple Azure-specific logic to the company graph.
- Let integration command details leak into the high-level graph state except through summaries,
  artifacts, and statuses.

### Stage 5: Real Routing And Repair Loop

Recommended branch:

```text
codex/repair-loop
```

Goal:

- Add the first real graph behavior: failed QA routes back to fullstack repair until it passes,
  blocks, or exhausts retry budget.

Company graph routing:

```text
qa
  -> if passed: deployment
  -> if failed and repair_attempts < max_repair_attempts: repair_request
  -> if failed and retry budget exhausted: blocked

repair_request
  -> fullstack_fix
  -> qa
```

Implementation notes:

- Use existing `10-fix-request.json` and `10-fix-request.md`.
- Add Codex fix-mode prompt construction.
- Track `repair_attempts` in `DeliveryState`.
- Preserve all repair evidence.
- Keep deployment after QA pass.

Acceptance criteria:

- A test can simulate QA fail -> repair -> QA pass.
- A test can simulate QA fail -> repair budget exhausted -> blocked.
- Repair attempts are visible in graph state and events.
- Handoff is not written for blocked runs unless it is an explicit blocked handoff artifact.

Do not:

- Add arbitrary parallelism yet.
- Let the repair loop run without a retry limit.
- Hide failed QA evidence after repair succeeds.

### Stage 6: Checkpoints, Interrupts, And Clean Integrations

Recommended branches:

```text
codex/graph-checkpoints
codex/integration-boundaries
```

Goal:

- Replace dev auto-confirm with production-style checkpoint/interrupt states.
- Move tool-specific code out of agents into integrations.

Checkpoint/interrupt examples:

```text
missing_credentials
approve_execution
qa_failed
approve_deployment
deployment_failed
```

Target integrations:

```text
src/agentic_company/integrations/
  azure/container_apps.py
  codex/cli.py
  docker/compose.py
  playwright/browser.py
  streamlit/apptest.py
```

Implementation notes:

- Console or chat UI should resume a checkpointed graph with user input.
- Agents call integrations.
- Integrations do not know graph flow.
- Dev auto-confirm may remain as an explicit local mode, but production mode should interrupt.

Acceptance criteria:

- Graph can stop at `missing_credentials` and resume after credentials are provided.
- Graph can stop at `approve_deployment` and resume after approval.
- Azure, Docker, Playwright, Streamlit, and Codex command logic has clear integration boundaries.
- Console no longer needs to know how each runner works internally.

Do not:

- Remove local dev auto-confirm until checkpointed flow is stable.
- Mix cloud-specific command construction into agent routing logic.
- Build a broad multi-tenant auth system in this stage.

## Branch Sequence

Use this order unless a blocker forces a smaller split:

1. `codex/add-delivery-graph-shell`
2. `codex/run-console-through-delivery-graph`
3. `codex/agent-wrappers`
4. `feature/agent-subgraphs/quality-agent-graph`
5. `codex/repair-loop`
6. `codex/deployment-agent-subgraph`
7. `codex/graph-checkpoints`
8. `codex/integration-boundaries`

## What Not To Do

- Do not start with a giant folder move.
- Do not rewrite QA and deployment at the same time as introducing LangGraph.
- Do not make the console the source of orchestration truth.
- Do not store workflow state only in chat messages.
- Do not make every agent an LLM agent just because the architecture supports it.
- Do not add LangGraph complexity that does not serve a current routing, checkpoint, or
  observability need.
- Do not weaken the existing QA/deployment evidence while refactoring.

## Definition Of Done For This Stage

- The current PoC still runs end-to-end.
- The primary delivery path is executed by a LangGraph graph.
- QA and deployment are represented as graph nodes or agent subgraphs, not hidden console helpers.
- Dev auto-confirm can run a full local generated-project deployment when `.env` and Azure CLI are
  ready.
- The graph state clearly records auto-confirmed steps.
- The codebase has a clear path to checkpoints, interrupts, repair loops, and deeper agent
  interactions.
