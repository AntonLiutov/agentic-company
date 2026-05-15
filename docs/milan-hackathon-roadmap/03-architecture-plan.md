# 03 Architecture Plan

## Architecture Agent Output

The architecture should keep the core system simple, observable, and provider-neutral. The first implementation should use deterministic Python orchestration and file-based handoffs. Model-specific execution should sit behind runner interfaces.

## Current Architecture

```text
requirements markdown
-> intake parser
-> project classifier
-> team assembler
-> workflow planner
-> implementation brief renderer
-> execution request
-> Codex runner
-> generated project folder
-> QA runner
-> deployment plan/request
-> optional Azure deployment runner
-> post-deploy QA
-> handoff summary
```

Current repository responsibilities:

- Agent registry
- Workflow definitions
- Schemas and contracts
- Planning pipeline
- Run artifact generation
- Quality tooling
- Local Streamlit control plane
- Codex execution backend for the Fullstack Engineer Agent
- Tool-executing QA Agent
- Azure Container Apps deployment runner for generated projects
- Shared live command streaming for Codex, QA, and deployment logs

## Target Architecture

```text
web console
-> orchestration API
-> planning pipeline
-> artifact store
-> runner abstraction
-> Codex worker
-> generated project repo/folder
-> QA + deployment + handoff artifacts
```

## Main Components

### 1. Web Console

Purpose:

- Accept requirements
- Trigger runs
- Display agent timeline
- Preview artifacts
- Trigger execution when ready

Initial stack recommendation:

- Streamlit for speed
- Later FastAPI + frontend if the demo needs more polish

### 2. Orchestration Core

Purpose:

- Own run lifecycle
- Call planning steps
- Write artifacts
- Write events
- Keep behavior deterministic where possible

This lives in `agentic-company`.

### 3. Artifact Store

Purpose:

- Store run outputs in a predictable folder structure
- Make every agent handoff inspectable
- Avoid relying on hidden chat history

Initial structure:

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
  09-handoff-summary.md
  11-deployment-plan.*
  12-deployment-request.*
  13-deployment-summary.md
  codex/
  qa/
  deployment/
  generated-project/
  events.jsonl
```

Future cleanup structure:

```text
runs/<run-id>/
  run-state.json
  artifacts/
  logs/
  evidence/
  generated-project/
  handoff/
```

### 4. Runner Abstraction

Purpose:

- Keep execution providers replaceable
- Let each agent have a stable identity
- Support Codex first, then Claude, Gemini, Figma, browser tools, and others

Initial concept:

```text
AgentRunner
  runner_id
  agent_id
  model
  working_directory
  input_artifacts
  expected_outputs
```

Recommended first implementation:

```text
CodexRunner
  reads implementation brief
  works in target project folder
  creates or edits files
  writes execution summary
```

Current implementation:

- `CodexCliRunner` executes the Fullstack Engineer Agent through `codex exec`.
- `QualityRunner` executes local, Docker, and browser checks against the generated project.
- `AzureDeploymentRunner` executes Azure CLI and Docker commands for generated projects.
- All command-based runners can append live command logs through a shared streaming primitive.

### 5. Agent Identity

Each agent should have:

- `agent_id`
- role definition from `agents/<agent>/`
- input artifacts
- output artifacts
- event stream
- optional model/provider config

Agents should communicate through artifacts first, not hidden conversation state.

## Framework Choice

### Current Recommendation

Introduce LangGraph now as the orchestration spine.

Reason:

- The first end-to-end PoC is working.
- The next features are graph-shaped: QA repair loops, deployment approvals, missing credential
  interrupts, upstream planning revisions, and downstream deployment failures.
- Adding LangGraph now lets the current linear flow become a graph without a later rewrite.
- Agents can remain provider-neutral because LangGraph owns flow, while agent wrappers and
  integrations own work.

The first graph should be linear and boring on purpose:

```text
planning -> engineering -> qa -> deployment_prepare_context -> deployment -> handoff
```

The production graph can then add conditional edges and checkpoints.

### LangGraph Target

Use LangGraph when:

- Workflows branch dynamically
- Agents loop until quality criteria are met
- Multiple agents run concurrently
- Human approval checkpoints become formal graph nodes
- Retry, compensation, or state recovery becomes painful

These conditions are now close enough that the platform should adopt the graph architecture before
the repair loop and approval model are implemented.

### When LangChain Might Make Sense

Consider LangChain when:

- We need many tool integrations quickly
- Retrieval and document pipelines become central
- We need common abstractions around model calls

## Codex Strategy

Codex should be the first real execution worker.

Use it for:

- Fullstack Engineer Agent
- QA Agent code review
- Documentation Agent handoff updates

Do not hardwire Codex into the planning model. Treat it as one provider behind the runner boundary.

## Agent Runtime Strategy

Not every agent should be implemented with the same runtime. The architecture should support a progression from deterministic rules to simple LLM calls, tool executors, Codex workers, and specialized external agents.

Recommended near-term split:

- Planning starts as a deterministic agent graph, then gains LLM-backed nodes.
- Engineering runs as a Codex-backed agent graph.
- QA runs as a tool-executing agent graph, then gains Codex repair/review nodes.
- Deployment runs as an Azure/Docker/Playwright tool agent graph.
- Handoff starts deterministic and can later gain LLM synthesis.
- Design can later use Claude plus Figma-oriented tooling.

See [09-agent-runtime-maturity-model.md](09-agent-runtime-maturity-model.md) and [10-agent-deep-dive.md](10-agent-deep-dive.md) for the detailed model.

## Deployment Strategy

Current generated-project deployment:

- Generated projects include Docker artifacts when the requirements do not rule them out.
- The QA Agent validates Docker Compose and runs Docker runtime E2E.
- The Deployment Agent targets Azure Container Apps in dev reuse mode.
- The platform itself remains local for now; this track deploys generated client projects, not the
  orchestration console.

Future platform deployment:

- Hosted orchestration console is still needed for a public hackathon demo.
- Vultr or another sponsor-aligned target can still be considered for the platform itself.
- Azure DevOps or GitHub Actions can come later once direct local deployment is reliable.

## Technical Risks

| Risk | Mitigation |
| --- | --- |
| Too much architecture before demo | Build one vertical slice first |
| Codex execution is hard to automate | Start with one Fullstack Engineer Agent action |
| Generated projects become messy | Use templates and strict acceptance criteria |
| Demo is too technical | Show visible agent steps and business outputs |
| Provider lock-in | Keep runner abstraction simple and explicit |
| QA failures require manual repair | Add Engineer <-> QA repair loop with retry budget |
| Artifact folders become noisy | Add a run manifest and product-facing artifact groups |

## Architecture Decision Records To Add Later

- ADR: File-based artifacts before graph orchestration
- ADR: Codex as first execution backend
- ADR: Streamlit console for MVP speed
- ADR: Provider-neutral runner interface
- ADR: Local run storage before database
- ADR: Azure Container Apps dev reuse for generated projects
- ADR: Handoff only after deployment succeeds
- ADR: LangGraph as the company orchestration spine
- ADR: Agent subgraphs composed by a delivery graph
- ADR: Dev auto-confirm before checkpointed approvals
