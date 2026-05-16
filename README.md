# agentic-company

Control plane for an agentic software delivery company.

This repository contains the control plane that turns requirements into a planned,
implemented, tested, deployed, and handed-off prototype. Generated application code lives in
run-local `runs/<run-id>/generated-project/` folders; this repo owns the agents, platform graph
runtime, contracts, console, and integrations that create and operate those projects.

## Current Status

The platform is an early PoC. The current default sample is a multi-service task tracker:

```text
examples/requirements/multi-service-task-tracker.md
```

This branch connects upstream planning to delivery through Head Agent. The active console graph is:

```text
Head Agent -> END
```

The platform graph runtime invokes Head Agent. Head coordinates planning and delivery through
specialist tools:

```text
Head Agent <-> Business Analyst
Head Agent <-> Architect
Head Agent <-> Project Manager
Head Agent <-> Team Lead
```

The Business Analyst agent reads `00-requirements.md`, receives the Head Agent request message,
runs a scoped Codex worker, and writes:

- `upstream-planning/business-analysis.md`
- `upstream-planning/business-analysis.json`
- `upstream-planning/business-analysis-request.json`

The Architect agent reads the Business Analyst artifacts and the Head Agent request message, runs a
scoped Codex worker, and writes:

- `upstream-planning/architecture.md`
- `upstream-planning/architecture.json`
- `upstream-planning/architecture.mmd`
- `upstream-planning/architecture-request.json`

The Project Manager agent reads the Business Analyst and Architect artifacts plus the Head Agent
request message, runs a scoped Codex worker, and writes:

- `upstream-planning/project-management/release-plan.md`
- `upstream-planning/project-management/release-plan.json`
- `upstream-planning/project-management/candidate-feature-queue.json`
- `upstream-planning/project-management/risks-and-dependencies.md`
- `upstream-planning/project-management/roadmap.csv`
- `upstream-planning/project-management/sprint-XX-plan.json`
- `upstream-planning/project-management-request.json`

Team Lead is no longer a platform graph node; it is a downstream specialist tool owned by Head
Agent, the same way BA, Architect, and Project Manager are coordinated.

## Runtime Shape

The platform is moving toward one common agent architecture:

```text
BaseDeliveryAgent
  -> AgentCapabilities + AgentCommunicationPolicy
  -> LangGraph shell
    -> prepare context
    -> LangChain create_agent executor
      -> allowed tools
      -> optional Codex worker tool
    -> apply result to delivery state
```

Head Agent, Business Analyst, Architect, Project Manager, Team Lead, Fullstack, QA, Deployment, and
Handoff use this shape or the same platform contracts. Head Agent and Team Lead coordinate through
specialist tools and do not get direct `codex_exec` permission. Business Analyst, Architect,
Project Manager, Fullstack, QA, Deployment, and Handoff each run a scoped AgentExecutor with
`codex_exec` as the specialist worker tool, then apply the result through their agent-owned graph
logic.

Agent communication is modeled as a platform primitive through `AgentMessage` and a run-local
message store. Communication remains policy-scoped: the platform can support agent-to-agent
messages, but each role receives explicit route and tool permissions.

## Repository Layout

- `examples/requirements/` - sample requirement documents for console and CLI runs
- `docs/` - milestones, architecture notes, roadmap, and agent contracts
- `src/agentic_company/agents/` - agent wrappers, agent-local graphs, and runtimes
- `src/agentic_company/platform/` - shared state, messages, artifacts, events, and security
- `src/agentic_company/integrations/` - Codex and command streaming helpers
- `src/agentic_company/orchestration/` - platform LangGraph runner and graph persistence
- `src/agentic_company/console/` - Streamlit operator console
- `tests/unit/` - fast unit tests grouped by architecture layer
- `tests/integration/` - slower end-to-end checks when present
- `runs/` - ignored local run output

## Quick Start

Install dependencies with `uv`:

```powershell
uv sync --extra dev --extra app
```

Run checks:

```powershell
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run --extra dev pytest --cov=agentic_company --cov-report=term-missing --cov-fail-under=75
```

Launch the local operator console:

```powershell
uv run --extra app streamlit run src/agentic_company/console/app.py --server.port 8502
```

The console lets you load the default multi-service sample, create an upstream planning run,
inspect the generated Head/BA/architecture/PM artifacts, and follow live Codex logs.

## CLI Usage

The Fullstack Codex CLI entry point exists for runs that contain
`delivery/execution-request.json`:

```powershell
uv run --extra dev agentic-run-codex runs\<run-id>
```

The active console flow writes artifacts under `runs/<run-id>/`, including:

- `00-requirements.md`
- Head Agent coordination artifacts under `head/`
- Business Analyst and Architect artifacts under `upstream-planning/`
- agent-owned Codex execution workspaces such as
  `upstream-planning/business-analyst/codex/` and `upstream-planning/architect/codex/`
- `.delivery-state.json`
- `events.jsonl`

## Environment Notes

Codex-backed workers may need access to local development tools and credentials:

- `OPENAI_API_KEY` for LangChain/OpenAI agent decisions;
- Docker or Rancher Desktop for local image/build checks;
- Azure CLI credentials for Azure Container Apps deployment;
- run-local generated project caches such as `.uv-cache`, `.npm-cache`, and `.deno-cache`.

The console can write run-local generated project `.env` files when a sample declares required
application configuration. The default multi-service sample does not require app-level OpenAI
credentials.

### Codex Worker Requirements

Business Analyst, Architect, Project Manager, Fullstack, QA, Deployment, and Handoff use Codex CLI
workers. Codex must be installed and available on `PATH`, or configured explicitly:

```powershell
$env:CODEX_BINARY="C:\path\to\codex.exe"
```

Business Analyst, Architect, and Project Manager use workspace-write Codex runs scoped to the run
directory and are prompted to write only their upstream planning artifacts. Their Codex prompts,
logs, raw events, and summaries live in agent-owned workspaces under
`upstream-planning/<agent>/codex/` so planning agents do not share one growing execution folder.
Engineering delivery workers run inside the generated project directory and receive the run
directory as an additional readable/writable context. For local prototype delivery the
engineering-worker default sandbox is:

```text
AGENTIC_CODEX_SANDBOX=danger-full-access
```

That default is intentional for trusted local runs that need dependency installation, Docker builds,
Azure CLI access, and deployment checks. It is not meant for untrusted code or untrusted
requirements. You can override it:

```powershell
$env:AGENTIC_CODEX_SANDBOX="workspace-write"
```

Allowed values:

```text
read-only
workspace-write
danger-full-access
```

By default, Codex subprocesses also inherit the shell environment:

```text
AGENTIC_CODEX_INHERIT_ENV=true
```

Set it to `false`, `0`, `no`, or `off` to skip passing
`shell_environment_policy.inherit=all`.

Codex reasoning effort defaults to high for platform agents:

```text
AGENTIC_CODEX_REASONING_EFFORT=high
```

Allowed values:

```text
low
medium
high
xhigh
```

The runner keeps dependency caches inside the generated project unless overridden:

```text
UV_CACHE_DIR      -> generated-project/.uv-cache
DENO_DIR          -> generated-project/.deno-cache
npm_config_cache  -> generated-project/.npm-cache
```

Optional overrides:

```text
AGENTIC_CODEX_UV_CACHE_DIR
AGENTIC_CODEX_DENO_DIR
AGENTIC_CODEX_NPM_CACHE
```

For host tooling, the runner attempts to expose existing user-level Docker and Azure configuration
to Codex workers:

```text
~/.azure
~/.docker
Docker CLI plugin directories, including Rancher Desktop plugin paths on Windows
```

This is the current pragmatic local-dev policy. Longer term, these permissions should move behind
agent/runtime policy configuration rather than living as delivery-worker defaults.

## Documentation

Useful starting points:

- [Docs index](docs/README.md)
- [MSD-002 Team Lead, BA, Architect, and PM](docs/msd-002-team-lead-ba-arch-pm/README.md)
- [Multi-Service Delivery Milestone](docs/multi-service-delivery-milestone/README.md)
- [Platform Rearchitecture](docs/platform-rearchitecture/README.md)
- [Agent Catalog](docs/agent-catalog.md)

## Development Notes

Keep changes small and agent-by-agent. The current migration path is:

1. keep Team Lead and specialist agents on the shared AgentExecutor runtime;
2. improve specialist tool registries beyond the initial `codex_exec` worker tool;
3. add Product/Project Manager on top of the same policy/message model;
4. add durable checkpointer/resume support for long-running handoffs between agents.

Do not put generated projects or local run artifacts into source control. Keep durable platform
contracts in `platform/`, and keep agent-specific behavior inside each agent module.

