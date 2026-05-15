# agentic-company

Core operating system for an agentic IT company.

This repository is intended to hold the reusable building blocks that power delivery across multiple client projects and internal PoCs:

- agent role definitions
- team assembly logic
- workflow orchestration
- shared platform contracts
- reusable templates
- delivery documentation

## Status

This is an early but working PoC control plane. The current vertical slice can plan a simple
web-app MVP, execute a Codex-backed Fullstack Agent run, QA the generated app locally and through
Docker/browser checks, deploy the generated app to Azure Container Apps after explicit confirmation,
run post-deployment browser QA, and then write a handoff summary.

The platform is still not a general autonomous software company. The strongest implemented roles are
Fullstack, QA, Deployment, and Handoff. Product, BA, Architecture, Design, Security, Memory,
and Support remain mostly deterministic, documented, or future-facing.

## Goals

- Standardize how work is received, scoped, staffed, delivered, and handed off
- Reuse the same agent patterns across multiple projects
- Keep delivery repositories separate from orchestration and company logic

## Suggested Early Scope

- Keep the master agent catalog and maturity model up to date.
- Harden the one working delivery lane before adding many project types.
- Add the Fullstack <-> QA repair loop so failed generated projects can be fixed and retested.
- Clean the artifact/run model so business users see a simple handoff while developers keep full
  evidence.
- Generalize beyond the current Streamlit LLM chat archetype.

## Repository Layout

- `docs/` - business and architectural documentation
- `src/agentic_company/platform/` - shared delivery state, artifact, event, and security contracts
- `src/agentic_company/integrations/` - reusable tool integrations used by agents
- `src/agentic_company/agents/` - specialist agent modules and agent-local graphs
- `src/agentic_company/orchestration/` - top-level LangGraph delivery runtime and routing
- `src/agentic_company/console/` - Streamlit web console, views, and local run services
- `agents/` - role definitions in both `README.md` and `agent.yaml` form
- `workflows/` - delivery flows by project type
- `templates/` - reusable templates for briefs, ADRs, QA reports, handoffs
- `tests/unit/` - fast unit tests grouped by architecture layer
- `tests/integration/` - artifact and workflow integration tests

## Agent Registry

The starting company roster contains 20 agents:

1. `Intake Agent`
2. `Team Assembler Agent`
3. `Sales / Discovery Agent`
4. `Product Manager Agent`
5. `Business Analyst Agent`
6. `Project / Delivery Manager Agent`
7. `UX / Product Designer Agent`
8. `Solution Architect Agent`
9. `Tech Lead Agent`
10. `Frontend Engineer Agent`
11. `Backend Engineer Agent`
12. `Fullstack Agent`
13. `AI / LLM Engineer Agent`
14. `Data Engineer Agent`
15. `DevOps / Platform Agent`
16. `QA Agent`
17. `Security Review Agent`
18. `Documentation / Handoff Agent`
19. `Support / Customer Success Agent`
20. `Knowledge / Memory Agent`

See [docs/agent-catalog.md](docs/agent-catalog.md) for the summarized catalog and `agents/` for full definitions.

## Quick Start

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Development

Run the test suite:

```powershell
uv run --extra dev ruff format .
uv run --extra dev ruff check .
uv run --extra dev pytest
```

The package currently targets Python 3.11+ and uses `setuptools` for packaging.

## Implemented Vertical Slice

The first useful milestone is now implemented for one narrow project type:

1. Accept an intake brief
2. Classify project type, complexity, and delivery mode
3. Assemble the smallest effective agent team
4. Select a workflow
5. Produce an implementation brief and execution request
6. Execute the generated project with Codex
7. Run QA with command, Docker, browser, screenshot, and transcript evidence
8. Prepare Azure deployment artifacts
9. Deploy the generated project to Azure Container Apps after explicit user confirmation
10. Run post-deploy browser QA and then write the final handoff

Run the Planning Agent:

```powershell
uv run --extra dev agentic-run-pipeline examples/requirements/web-app-mvp-chat.md
```

If you are using an already activated environment, install the package first with `pip install -e .[dev]`, then run `python -m agentic_company.agents.planning.run examples/requirements/web-app-mvp-chat.md`.

The command writes structured artifacts to `runs/<run-id>/`. The planning loop remains
framework-light and file-based on purpose. LangGraph now coordinates the active Planning,
Fullstack, QA, Deployment, and Handoff agents while some older deterministic internals are still
being moved behind those specialist boundaries.
Planning-owned JSON Schema contracts live with the Planning Agent under
`src/agentic_company/agents/planning/schemas/`; future cross-agent contracts should live under
`platform/` instead of a generic root bucket.

Each run also writes `events.jsonl`, an append-only trace of planning and artifact events.

Run the current execution request with the real Codex CLI:

```powershell
uv run --extra dev agentic-run-codex runs\<run-id>
```

The Codex runner reads `06-execution-request.json`, passes the planning artifacts to
`codex exec`, works inside the run's `generated-project/` directory, and writes
`07-execution-summary.md`. Successful executions continue into QA and deployment readiness:
`08-qa-report.md` records local/Docker/browser QA, while `11-deployment-plan.*` and
`12-deployment-request.*` prepare the Azure Container Apps deployment. Final handoff is written only
after an explicit deployment succeeds and post-deployment QA passes.

QA evidence is kept under `qa/test-plan.json`, `qa/results.json`, and `qa/commands.log`. The QA
agent checks generated artifacts, obvious secret leaks, README operability, dependency sync, Python
compilation, Streamlit AppTest coverage for missing/configured credentials, Docker Compose config,
Docker runtime E2E, and required Playwright live chat E2E checks that launch the generated app,
send a real message, wait for an assistant response, and record browser screenshots plus
transcripts. Docker runtime QA also writes `qa/docker/build-summary.json` and
`qa/docker/runtime-command.log` so slow image builds can be inspected by step. If QA fails, the
review runner writes `10-fix-request.json` and `10-fix-request.md` as the structured input for the
next Fullstack Agent / Codex repair pass.

When explicitly confirmed in the console, the Azure Deployment Runner deploys the generated app,
writes `13-deployment-summary.md`, runs post-deployment Playwright chatbot QA against the public
Azure URL, and only then writes `09-handoff-summary.md`. Dev deployments reuse stable Azure
resources by default so repeated runs update the existing dev Container App instead of creating new
infrastructure each time. Docker QA uses a stable dev Compose project name for the same reason, so
repeated local QA runs reuse/replace the same generated-project image namespace.

Codex telemetry is kept under `codex/prompt.md`, `codex/execution.log`, and `codex/events.jsonl`.
The console has one `Live Logs` view that combines Codex commentary, workflow events, QA command
logs, deployment command logs, diff/file summaries, and raw evidence. The console starts Codex and
deployment work in background threads and refreshes while they are running, so logs become visible
before each stage completes. QA, deployment, and Codex command output share the same streaming
primitive in `agentic_company.integrations.commands`, so new agents can add live developer logs
without inventing another log format.

## Local Planning Console

Launch the local Streamlit web console:

```powershell
uv run --extra app streamlit run src/agentic_company/console/app.py --server.port 8502
```

The console lets you paste requirements, load the sample requirements, run the deterministic
Planning Agent, inspect generated artifacts, and read the `events.jsonl` timeline without
opening a terminal. It can also generate a deterministic Streamlit chat starter in the run's
`generated-project/` folder or run the real Codex CLI execution path after explicit confirmation.
When the intake artifact lists required configuration such as `OPENAI_API_KEY`, the console can
write those values to the run-local `generated-project/.env` before execution; saved values are not
shown again and stay under ignored `runs/` output.
The current planning runtime is intentionally shown as `L0 Deterministic`; execution is represented
as `L6 Codex Agent` once the handoff reaches the Fullstack Agent.
It uses port `8502` so generated demo apps can keep their default development ports free.

Console and runner logs are written to the terminal. Set `AGENTIC_COMPANY_LOG_LEVEL=DEBUG` before
launching the console or CLI commands when you want more detailed local diagnostics.
