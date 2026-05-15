# 05 Backlog

## Backlog Priority Key

- `P0`: needed for a credible demo
- `P1`: strong improvement for hackathon competitiveness
- `P2`: useful later, not urgent

## Current Backlog State

The original weekend P0s for local console, artifact viewing, event timeline, Codex execution,
generated Streamlit chat, generated project QA, and Azure dev deployment are implemented for the
first vertical slice. The remaining P0 work is no longer "make it run once"; it is "make the loop
self-correcting, less noisy, and credible beyond one app archetype."

Current highest priorities:

1. Engineer <-> QA repair loop.
2. Artifact/run manifest cleanup.
3. Up-to-date demo script and submission materials.
4. One additional generated-project archetype.
5. Azure deployment UX hardening.

## Epic 1: Planning Console

### P0: Add Local Web Console

Status: done

User story:

As a user, I want to paste project requirements into a web UI so I can run the agentic planning pipeline without using the CLI.

Acceptance criteria:

- App launches locally.
- User can paste requirements text.
- User can load the sample requirements.
- User can run the pipeline.
- The app shows output folder path.

### P0: Artifact Viewer

Status: done, needs grouping/polish

User story:

As a user, I want to inspect each generated artifact so I can understand what the agents produced.

Acceptance criteria:

- Intake brief is visible.
- Classification is visible.
- Staffing decision is visible.
- Workflow plan is visible.
- Implementation brief is visible.

### P0: Event Timeline

Status: done, needs friendlier progress copy and less noisy dev output

User story:

As a user, I want to see the agent event timeline so I can trust the system flow.

Acceptance criteria:

- Timeline reads `events.jsonl`.
- Each event shows agent id, event name, and artifact when present.
- Timeline updates after a run.

## Epic 2: Runner Architecture

### P0: Define Agent Runner Interface

Status: partially done through execution request and runner classes

User story:

As a developer, I want a runner interface so different execution backends can be added without changing the orchestration core.

Acceptance criteria:

- Interface has agent id, model, working directory, input artifacts, and expected outputs.
- Initial implementation can run behind the Engineering Agent boundary.
- Runner writes events.

### P0: Codex Runner Spike

Status: done for the Fullstack Engineer Agent

User story:

As a user, I want the Fullstack Engineer Agent to use Codex to generate an MVP from the implementation brief.

Acceptance criteria:

- Runner creates a controlled target project folder.
- Runner passes implementation brief to Codex.
- Runner records execution start and completion.
- Runner writes execution summary.

### P1: Runner Provider Config

Status: partially done for Codex model selection; broader provider config remains open

User story:

As a developer, I want provider config so we can later add Claude, Gemini, Figma, or other tools.

Acceptance criteria:

- Config supports provider name and model.
- Default generic model remains `gpt-4o-mini`.
- Codex model can be configured separately.

## Epic 3: Generated Project

### P0: Streamlit LLM Chat Starter

Status: done for the first generated-project archetype

User story:

As a user, I want the system to generate a simple Streamlit LLM chat app so the demo proves execution.

Acceptance criteria:

- Generated app has `app.py`.
- Generated app reads `OPENAI_API_KEY`.
- Generated app reads `DEFAULT_MODEL`.
- Missing API key shows a friendly message.
- README explains setup.

### P1: Generated Project QA

Status: done, upgraded beyond the original P1 scope

User story:

As a user, I want the QA Agent to review the generated project so the output feels delivery-ready.

Acceptance criteria:

- QA report exists with structured results and command evidence.
- Known limitations are listed.
- Manual test steps are clear.
- Local and Docker browser evidence is captured.
- Live chat flow waits for an assistant response.

### P0: Engineer <-> QA Repair Loop

User story:

As a user, I want failed QA evidence to go back to the Fullstack Engineer Agent automatically so the
system can fix and retest the generated project without me manually copying logs.

Acceptance criteria:

- Failed QA creates a structured repair request.
- Repair request includes QA report, structured results, command log, Docker summary, browser
  transcripts, and screenshot references when available.
- Codex can run in repair mode against the existing generated project.
- QA reruns after repair.
- The loop has a retry budget.
- Final state clearly says passed, fixed, blocked, or retry budget exhausted.

### P1: Artifact Run Manifest

User story:

As a user, I want the run output grouped into simple product-facing sections while developers can
still inspect raw logs and evidence.

Acceptance criteria:

- Run has one manifest that classifies artifacts as user-facing, evidence, logs, scripts, or
  internal telemetry.
- Console artifact view groups files by purpose.
- Handoff references only the important delivery artifacts.
- Raw logs remain available but do not dominate the default UI.

## Epic 4: Workflow And Schema Evolution

### P1: Load Workflow From Markdown Or YAML

User story:

As a developer, I want workflow steps to be data-driven so new workflows do not require code edits.

Acceptance criteria:

- `web-app-mvp` workflow can be loaded from a structured file.
- Planner still emits the same workflow plan artifact.

### P1: Validate Artifacts Against Schemas

User story:

As a developer, I want artifacts validated so agent handoffs remain stable.

Acceptance criteria:

- JSON artifacts are validated against schema files.
- Validation errors are written to events.
- Tests cover schema validation.

## Epic 5: Hackathon Demo

### P0: Demo Script

User story:

As a presenter, I want a reliable demo script so I can explain the product confidently.

Acceptance criteria:

- Script covers problem, product, live run, generated output, and business value.
- Script has a 60-second version.
- Script has a 3-minute version.

### P0: Public README

User story:

As a judge or investor, I want a clear README so I can understand and run the project.

Acceptance criteria:

- README explains the product in plain language.
- README has setup and demo commands.
- README links to architecture and roadmap docs.

### P1: Hosted Demo

Status: partially done for generated apps; orchestration console is still local

User story:

As a judge, I want a public app URL so I can try the project.

Acceptance criteria:

- Generated app can be deployed.
- Orchestration console public demo URL exists.
- Demo sample input is included.
- Basic failure states are friendly.

## Epic 8: Deployment UX

### P0: Azure Dev Reuse Deployment

Status: done for generated Dockerized Streamlit apps

User story:

As a user, I want to deploy the generated project to a stable Azure dev environment so iteration does
not create a new resource group and image namespace every time.

Acceptance criteria:

- Deployment uses local Azure CLI login after explicit confirmation.
- Resource group, ACR, and Container Apps environment are reused.
- Container App is created if missing and updated if present.
- Required app secrets come from the generated project's `.env`.
- Deployment summary includes public URL, resources, command evidence, and teardown command.
- Post-deploy browser QA runs against the public URL before handoff.

### P1: Deployment Controls And Cleanup

User story:

As a developer, I want safer deployment controls so I can see which Azure account, subscription, and
resources are about to be used.

Acceptance criteria:

- Console shows selected Azure subscription before deployment.
- User can choose or confirm target environment.
- UI exposes teardown guidance or action.
- Deployment logs distinguish "resource existed" from "resource created".
- Failed deployments produce a repairable artifact and clear next step.

### P1: Submission Assets

User story:

As a hackathon participant, I want polished submission assets so the project can compete well.

Acceptance criteria:

- Cover image exists.
- Slides exist.
- Demo video exists.
- Public GitHub repo is up to date.

## Epic 6: Business And Investor Story

### P1: Landing Narrative

User story:

As a viewer, I want a clear product narrative so I understand why this matters.

Acceptance criteria:

- One-sentence pitch exists.
- Problem and solution are clear.
- Demo connects to enterprise value.

### P2: Pricing And Market Notes

User story:

As a founder, I want early monetization hypotheses so the project can become a company.

Acceptance criteria:

- Target customer segments are listed.
- Possible pricing models are listed.
- Investor questions are anticipated.

## Epic 7: Agent Runtime Maturity

### P0: Define Agent Maturity Levels

User story:

As a developer, I want a clear maturity model for agents so we know when to use hardcoded rules, simple LLM calls, Codex, LangChain, LangGraph, or specialized external tools.

Acceptance criteria:

- Agent levels are documented.
- Each level has a recommended use case.
- The first weekend level is identified for each key agent.

### P0: Define Agent Deep Dives

User story:

As a product builder, I want each company agent described in practical detail so the roadmap clearly shows how the system grows.

Acceptance criteria:

- Every current company agent has responsibilities documented.
- Every agent has expected artifacts.
- Every agent has a first implementation strategy.
- Advanced paths such as voice intake, Codex execution, and Claude + Figma design are captured.

### P1: Add Agent Runtime Metadata

User story:

As a developer, I want run events to include agent runtime metadata so users can see whether a step was deterministic, LLM-backed, Codex-backed, or tool-backed.

Acceptance criteria:

- Events can include `agent_version`.
- Events can include `maturity_level`.
- UI can display the runtime type later.

## Immediate Weekend Task Order

1. Add Engineer <-> QA repair loop.
2. Add artifact/run manifest grouping.
3. Polish demo script and submission README around the real end-to-end flow.
4. Add one more generated-project archetype.
5. Harden Azure deployment UX and cleanup controls.
