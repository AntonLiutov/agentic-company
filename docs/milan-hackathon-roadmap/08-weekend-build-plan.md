# 08 Current Build Plan

This is the tactical plan for turning the planning console into a real execution loop.

## Current Goal

The project now has a local console that can plan, execute through Codex, QA the generated app,
deploy the generated app to Azure after confirmation, run post-deploy QA, and produce a handoff.
The next goal is to make that loop self-correcting, less noisy, and easier to generalize.

Current loop:

1. Open the planning console.
2. Paste or load requirements.
3. Run deterministic planning.
4. Save required credentials.
5. Execute through the Codex-backed Fullstack Engineer Agent.
6. Run QA automatically.
7. Inspect QA evidence, browser transcripts, screenshots, and Docker logs.
8. Deploy the generated app to Azure Container Apps after explicit confirmation.
9. Run post-deploy browser QA against the public URL.
10. Inspect the deployment summary and final handoff.

Next loop:

11. When QA fails, create a structured fix request.
12. Run Codex against the failure evidence.
13. Rerun QA until the project passes or the retry budget is exhausted.

## Workstream A: Planning Console

Priority: P0

Status: done

Tasks:

- Choose location for app code.
- Build basic Streamlit app.
- Add text area for requirements.
- Add "load sample" button.
- Add "run pipeline" button.
- Show output run id and folder.
- Render artifact tabs.
- Render event timeline.

Definition of done:

- User can run the existing pipeline without opening a terminal.

## Workstream B: Artifact UX

Priority: P0

Status: partially done

Tasks:

- Display JSON artifacts in readable format.
- Display Markdown implementation brief.
- Show selected team clearly.
- Show workflow phases clearly.
- Add small status indicators for each stage.

Definition of done:

- A viewer can understand the agent flow in under one minute.

## Workstream C: Execution Bridge

Priority: P0

Status: done

Tasks:

- Add `06-execution-request.json`.
- Include target project folder.
- Include agent id.
- Include model/provider preference.
- Include input artifacts.
- Include expected outputs.
- Add event for execution request creation.

Definition of done:

- Planning run produces everything a Codex runner needs next.

## Workstream D: Codex Runner Spike

Status: done

Tasks:

- Codex execution runs against the generated project directory.
- The console can start Codex in the background.
- Codex telemetry is rendered as Commentary, Events, Command, Diff / Files, and Raw views.
- Successful execution automatically proceeds into QA and handoff.

Definition of done:

- The codebase has a working Codex execution backend and visible execution telemetry.

## Workstream F: QA Observability

Priority: P0

Status: mostly done

Tasks:

- Keep QA results structured in `qa/results.json`.
- Keep command evidence in `qa/commands.log`.
- Capture browser transcripts and screenshots for live chat QA.
- Capture Docker runtime output in `qa/docker/runtime-command.log`.
- Summarize Docker build bottlenecks in `qa/docker/build-summary.json`.
- Make QA reports explain what was actually proven and what remains uncovered.

Definition of done:

- A user can tell whether a pass is meaningful and where time was spent when Docker QA is slow.

## Workstream G: QA Failure Repair Loop

Priority: P0

Status: next major task

Tasks:

- Convert failed QA checks into a structured fix request for Codex.
- Include the QA report, command log, browser transcript, screenshots, and Docker summary as input.
- Run Codex in fix mode against the generated project.
- Rerun QA after the fix.
- Add retry limits and clear terminal states.

Definition of done:

- A generated project can move from failed QA to passing QA without the user manually copying logs
  into a new prompt.

## Workstream E: Demo Story

Priority: P0

Status: needs refresh around the real Azure-deployed flow

Tasks:

- Write 60-second demo script.
- Write 3-minute demo script.
- Prepare one excellent sample input.
- Prepare one screenshot-worthy output run.
- Add README section for demo.

Definition of done:

- We can explain the project without rambling.

## Suggested Build Order

1. Codex fix-mode runner.
2. QA rerun loop with retry budget.
3. Handoff summary that distinguishes passed, fixed, manually blocked, and deployment-blocked runs.
4. Run manifest and artifact grouping for a cleaner console.
5. Demo script refresh around the real Azure-deployed flow.
6. Azure deployment controls: account/subscription display, teardown guidance, and clearer
   create-vs-reuse reporting.
7. A second generated-project archetype.

## Stop Conditions

Do not chase yet:

- LangGraph
- LangChain
- Authentication
- Database
- Multi-provider execution
- Fancy UI polish beyond making progress and failure states clear

The winning move now is a trustworthy execution loop.

## Near-Term State We Want

The project should be able to say:

> We have an agentic planning console that turns raw requirements into delivery artifacts, runs
> Codex implementation, validates the generated app through real QA, deploys it to Azure, verifies
> the public URL, and can use QA evidence to request fixes automatically.

That is enough to start feeling like a small autonomous delivery company rather than a planning
demo.
