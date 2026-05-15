# Milan Hackathon Roadmap

This folder is the working plan for turning `agentic-company` into a Milan AI Week hackathon-ready product.

Status note:

This roadmap captured the first vertical-slice PoC. It remains useful product and demo context, but
the active engineering plan has moved to
[Platform Rearchitecture](../platform-rearchitecture/README.md). The new stage introduces a
LangGraph graph-of-agents architecture so the current linear PoC can grow into repair loops,
checkpointed approvals, and deeper agent interactions without a later rewrite.

The project direction is:

> An autonomous AI delivery team that turns a raw business idea into a scoped, staffed, implemented MVP.

The current repository has moved beyond the planning foundation. It now contains a local control
plane that can plan a simple web-app MVP, run a Codex-backed Fullstack Engineer Agent, validate the
generated app with local/Docker/browser QA, deploy the generated app to Azure Container Apps after
explicit confirmation, run post-deployment browser QA, and write a final handoff.

This is still a vertical-slice PoC, not the final autonomous company platform. The next stage is the
global platform rearchitecture: introduce LangGraph as the orchestration spine, represent current
linear steps as graph nodes, convert major roles into agent subgraphs, and then add repair loops,
checkpoints, interrupts, and richer upstream/downstream interactions.

## North Star

Build a web-based enterprise agent system for AI Week that demonstrates a full project delivery loop:

1. A user submits a raw product idea or requirements document.
2. The Intake Agent normalizes it into a brief.
3. The Team Assembler Agent selects the smallest useful agent team.
4. Product, architecture, delivery, and QA agents produce structured artifacts.
5. A worker agent uses Codex to create or update a real starter project.
6. The system shows a transparent timeline of decisions, artifacts, logs, and handoffs.

## Hackathon Fit

| Hackathon Expectation | Our Product Fit |
| --- | --- |
| Autonomous agents beyond copilots | Agents produce artifacts and Codex modifies real generated project files |
| Agentic workflows | Requirements -> planning -> execution -> QA -> deployment -> handoff |
| Enterprise utility | Helps teams scope, staff, and start internal tools or MVPs faster |
| Collaborative systems | Multiple specialized agents coordinate through artifact contracts |
| Deployable web app | The local control plane deploys generated client apps to Azure Container Apps |
| Business value | Reduces ambiguity, startup cost, and handoff friction in software delivery |

## Roadmap Files

- [01-intake-brief.md](01-intake-brief.md) - consolidated intake from our conversation and hackathon context
- [02-product-and-business-analysis.md](02-product-and-business-analysis.md) - product framing, users, value, risks, and requirements
- [03-architecture-plan.md](03-architecture-plan.md) - system architecture and execution strategy
- [04-delivery-roadmap.md](04-delivery-roadmap.md) - phases, milestones, epics, and sprint plan
- [05-backlog.md](05-backlog.md) - actionable backlog with priorities and acceptance criteria
- [06-demo-and-investor-readiness.md](06-demo-and-investor-readiness.md) - demo story, submission assets, and pitch positioning
- [07-agent-execution-map.md](07-agent-execution-map.md) - agent responsibilities, artifacts, and handoffs
- [08-weekend-build-plan.md](08-weekend-build-plan.md) - tactical weekend build order
- [09-agent-runtime-maturity-model.md](09-agent-runtime-maturity-model.md) - implementation levels from hardcoded rules to Codex and specialized agents
- [10-agent-deep-dive.md](10-agent-deep-dive.md) - detailed responsibilities, interactions, artifacts, and maturity path for every company agent
- [11-generated-project-deployment-plan.md](11-generated-project-deployment-plan.md) - Azure deployment path for generated client projects

## Current State

Done:

- Initial repository structure committed
- Agent registry established
- First deterministic `web-app-mvp` planning pipeline implemented
- Pipeline writes intake, classification, staffing, workflow, implementation brief, and `events.jsonl`
- Ruff, pytest, and CI quality checks added
- CLI entry point added through `agentic-run-pipeline`
- Local Streamlit console for requirements intake, artifact review, credentials, execution, QA,
  deployment, and live logs
- Codex CLI runner for the Fullstack Engineer Agent
- Generated Streamlit LLM chat app path with uv, Docker, Docker Compose, and secret-safe `.env`
  handling
- QA Agent with expected-file checks, secret scan, README checks, uv sync, Python compile,
  Streamlit AppTest, Docker Compose config, Docker runtime E2E, Playwright live chat E2E,
  screenshots, transcripts, and structured reports
- Deployment planning artifacts and Azure Container Apps deployment runner for generated projects
- Post-deployment Playwright chatbot QA against the public Azure URL
- Handoff summary written after deployment succeeds
- Shared command streaming primitive for Codex, QA, and deployment logs

Not done yet:

- Dynamic workflow loading from files
- Automatic Engineer <-> QA repair loop after failed QA
- Multiple generated project archetypes beyond the Streamlit LLM chat MVP
- LLM-backed Product, BA, Architecture, Design, Security, and Documentation agents
- Clean product-grade artifact grouping for business users
- Azure environment management beyond the current stable dev reuse mode
- Hosted orchestration console
- Submission video, slides, cover image, and public demo URL

## Recommended Next Move

Move to the platform rearchitecture stage:

1. Add a LangGraph delivery graph shell around the current working PoC.
2. Run the existing planning, Codex, QA, deployment, and handoff stages as graph nodes.
3. Temporarily allow dev auto-confirm for credentials/deployment when `.env`, Docker, and Azure CLI
   are already ready.
4. Convert QA and deployment into first-class agent nodes/subgraphs.
5. Add checkpoint/interrupt support later for missing credentials, approvals, and repair decisions.

Then close the Engineer <-> QA repair loop and add a second generated-project archetype.
