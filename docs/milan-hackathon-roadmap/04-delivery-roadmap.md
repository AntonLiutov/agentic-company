# 04 Delivery Roadmap

## PM Agent Output

This roadmap is optimized for fast weekend progress and a credible Milan AI Week hackathon demo.

## Phase 0: Foundation

Status: done

Goal:

- Establish the core repo and first planning pipeline.

Completed:

- Initial repository structure
- Agent registry
- `web-app-mvp` deterministic pipeline
- Structured artifacts
- Event tracing
- Ruff, pytest, CI
- CLI command

## Phase 1: Visible Planning Console

Status: done

Target:

- Completed local PoC

Goal:

- Make the planning pipeline visible and usable through a simple web interface.

Epics:

- Requirements input UI
- Run trigger
- Artifact viewer
- Event timeline
- Run history list

Acceptance criteria:

- User can paste requirements into a web form. Done.
- User can run the pipeline from the UI. Done.
- UI shows generated artifacts and run status. Done.
- UI shows event timeline and live logs from run artifacts. Done.
- UI works locally without external services for planning. Done.

## Phase 2: Execution Runner MVP

Status: done for the first Codex path

Target:

- Completed local PoC

Goal:

- Add the first real execution step using Codex.

Epics:

- Runner interface
- Codex runner prototype
- Target project folder creation
- Fullstack Engineer Agent execution prompt
- Execution summary artifact

Acceptance criteria:

- User can start Codex execution from the console after confirmation. Done.
- System creates a target folder outside the core source package. Done.
- Codex receives the implementation brief and creates a basic app. Done.
- Run folder records execution events and Codex telemetry. Done.
- Generated app has README, setup notes, uv metadata, Docker artifacts, and summary. Done.

## Phase 3: QA And Handoff

Status: partially done, with repair loop missing

Target:

- Current active quality phase

Goal:

- Show that delivery does not stop at code generation.

Epics:

- QA report with automated check evidence. Done.
- Local, Docker, and browser checks for the generated Streamlit app. Done.
- Structured fix request when QA fails. Started.
- Automatic Codex repair and QA rerun. Missing.
- Documentation handoff summary after deployment succeeds. Done.
- Known limitations report. Done in QA report, needs product polish.

Acceptance criteria:

- QA Agent produces a review report backed by command evidence. Done.
- QA Agent captures browser transcripts, screenshots, and Docker build evidence. Done.
- Documentation Agent produces handoff notes only after deployment succeeds. Done.
- Demo can show planning, execution, QA, deployment, and handoff in one flow. Done for one app type.
- Failed QA can automatically trigger a Codex repair loop. Missing.

## Phase 3.5: Deployment Of Generated Projects

Status: done for Azure dev reuse mode

Goal:

- Prove that the platform can deploy the generated client project, not just generate local files.

Completed:

- Deployment plan and deployment request artifacts
- Explicit user-confirmed Azure deployment action in the console
- Local Azure CLI credential strategy
- Stable dev resource group, ACR, Container Apps environment, and per-project Container App name
- Docker image build and push to ACR
- Container App create-or-update flow
- Container App secrets and env var wiring
- Public URL capture
- Post-deployment Playwright chatbot QA
- Handoff summary after successful deployment

Remaining:

- Better Azure subscription/environment UX
- Teardown and cost controls in the UI
- Rollback or revision handling
- More deployment targets or CI/CD modes

## Phase 4: Demo Productization

Status: in progress

Target:

- Before hackathon build phase

Goal:

- Make the product easy to understand, host, and present.

Epics:

- Hosted orchestration console
- Clean sample inputs
- Polished landing explanation inside app
- Demo script
- Submission README
- Slides
- Demo video

Acceptance criteria:

- Public URL exists for generated deployed apps. Done.
- Public hosted URL exists for the orchestration console. Missing.
- Public GitHub repo is clean. In progress.
- One sample run works end-to-end. Done locally with Azure deployment confirmation.
- A 3-minute demo video can be recorded without improvising. Missing.

## Phase 5: Hackathon Adaptation

Target:

- May 13-19 build phase

Goal:

- Adapt the product to sponsors, judging criteria, and feedback.

Possible sponsor alignment:

- Vultr: deploy the orchestration console and generated backend on Vultr.
- Google Gemini: optionally add Gemini as a planning or multimodal analysis provider.
- Featherless: optionally add open-source model provider support.

Do not chase every sponsor. Prioritize the sponsor path that strengthens the main story.

## Phase 6: Platform Rearchitecture

Status: active next stage

Goal:

- Move from a working PoC chain to a real graph-of-agents platform architecture.

Epics:

- LangGraph delivery graph around the current linear flow
- Agent subgraphs for planning, engineering, QA, deployment, and handoff
- Shared delivery state and graph runtime
- Temporary dev auto-confirm for credentials/deployment when `.env`, Docker, and Azure CLI are ready
- Later checkpoint/interrupt support for credentials, approvals, repair decisions, and deployment
  decisions
- Console integration through graph state instead of direct runner calls

Acceptance criteria:

- Current PoC still runs end-to-end.
- The current linear delivery path is executed as a LangGraph graph.
- QA and deployment are represented as graph nodes or agent subgraphs.
- Dev auto-confirm is visible in graph state and logs.
- The architecture can add QA repair loops without another orchestration rewrite.

## Sprint Plan

### Sprint 1: Weekend Planning Console

Goal:

- Turn the CLI pipeline into a usable local demo.

Status: done

Tasks:

- Create `apps/planning_console` or `src/agentic_company/ui`.
- Add requirements text input.
- Add sample requirements loader.
- Add run button.
- Render artifact tabs.
- Render event timeline.
- Add README run command.

Definition of done:

- A non-technical viewer can understand what each agent did.

### Sprint 2: Codex Execution Spike

Goal:

- Prove one worker agent can create a project.

Status: done

Tasks:

- Define `AgentRunner` contract.
- Add `CodexRunner` placeholder/prototype.
- Define target project folder rules.
- Build prompt from implementation brief.
- Write execution summary.

Definition of done:

- One execution step can create or update files in a controlled target project folder.

### Sprint 3: End-To-End Demo

Goal:

- Show idea-to-starter-app flow.

Status: done for one Streamlit LLM chat archetype

Tasks:

- Connect planning console to execution trigger.
- Add generated app preview instructions.
- Add QA and handoff artifacts.
- Prepare a canned demo run.

Definition of done:

- Demo story works from raw idea to starter project.

### Sprint 4: Hackathon Submission Polish

Goal:

- Turn the prototype into a presentable product.

Status: in progress

### Sprint 5: Platform Graph Spine

Goal:

- Introduce LangGraph without breaking the current PoC.

Tasks:

- Add LangGraph dependency.
- Add `DeliveryState`.
- Add graph nodes that wrap existing planning, Codex, QA, deployment, and handoff behavior.
- Add dev auto-confirm mode for credentials and deployment.
- Add tests proving the linear graph path.

Definition of done:

- The current delivery path can run through the graph runtime.

### Sprint 6: Repair Loop And Generalization

Goal:

- Move from a one-way delivery lane to a self-correcting delivery loop.

Tasks:

- Convert failed QA checks into repair inputs.
- Add Codex fix-mode runner.
- Rerun QA after repair with a retry budget.
- Show fixed, blocked, and exhausted states in the console.
- Add one additional generated-project archetype.
- Reduce artifact clutter with a run manifest or grouped artifact model.

Definition of done:

- A failed generated project can be repaired and retested without the user manually copying QA logs
  into a new prompt.

Tasks:

- Host demo.
- Clean GitHub README.
- Record video.
- Create slides.
- Prepare pitch.
- Add clear architecture diagram.

Definition of done:

- Submission package is complete.

## Release Milestones

| Milestone | Result |
| --- | --- |
| M1 | CLI planning pipeline |
| M2 | Local planning console |
| M3 | Codex execution runner |
| M4 | End-to-end generated starter project |
| M5 | QA, deployment, and handoff loop for one project type |
| M6 | LangGraph delivery graph spine |
| M7 | Engineer <-> QA repair loop |
| M8 | Submission assets |
