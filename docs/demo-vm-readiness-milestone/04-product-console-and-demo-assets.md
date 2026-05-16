# 04 Product Console And Demo Assets

## Product Console Direction

The current Streamlit console proved the workflow, but the demo needs to feel like a product.

Target UI:

```text
Left sidebar:
  projects
  runs
  skills/agents
  settings

Center:
  chat or execution timeline
  live agent messages
  current stage and active task
  concise business-facing status

Right panel:
  sprint board
  artifact list
  selected artifact preview
  deployment URL
  QA/handoff status

Debug mode:
  raw logs
  tool calls
  Codex output
  event stream
```

Design principles:

- Business-first status by default.
- Debug details available but not visually dominant.
- Artifacts are evidence, not hidden files.
- Board status must reflect real pipeline state.
- Deployment URL should be visible as soon as it exists.
- Handoff should be readable by a non-technical viewer.

## Screenshot Plan

Capture these states:

| ID | Screenshot | Purpose |
| --- | --- | --- |
| SS-01 | Initial project request | Shows simple user input |
| SS-02 | Planning artifacts ready | Shows BA/Architecture/PM output |
| SS-03 | Sprint board in progress | Shows task ownership and active work |
| SS-04 | QA passed | Shows validation loop |
| SS-05 | Deployment completed | Shows public URL and Azure result |
| SS-06 | Handoff ready | Shows business-facing delivery package |
| SS-07 | Debug mode | Shows transparency for technical reviewers |
| SS-08 | Final report | Shows the complete outcome |

## Presentation Outline

1. Problem: small teams lose time turning ideas into scoped, deployed MVPs.
2. Product: an AI delivery coordinator that plans, builds, tests, deploys, and reports.
3. Workflow: request -> plan -> sprint board -> Codex implementation -> QA -> Azure deploy -> handoff.
4. Proof: show the successful run, QA status, deployment URL, and handoff.
5. Why it matters: not just code generation; it is delivery coordination with evidence.
6. Current state: working PoC, deployable generated app, console visibility.
7. Next step: polished web console and VM-hosted demo.
8. Ask: feedback, pilot users, hackathon/demo evaluation.

## Video Script Outline

Target length: 2-3 minutes.

```text
0:00 - 0:20
Introduce the problem and one-sentence product promise.

0:20 - 0:45
Show the user request and planning output.

0:45 - 1:20
Show the board executing: Fullstack, QA, deployment, handoff.

1:20 - 1:50
Open the deployed app and show it working.

1:50 - 2:20
Open the handoff report and explain evidence.

2:20 - 2:45
Close with why this is different from a coding assistant.
```

## Short Pitch

Agentic Company turns a raw product idea into a scoped, implemented, tested, deployed MVP with
human-readable delivery evidence. It coordinates specialist AI roles, uses Codex for real code work,
validates results with QA, deploys to Azure, and produces a handoff that business and technical
stakeholders can both understand.

The current milestone proved the end-to-end loop. The next milestone makes it portable to a VM and
polishes the console into a real product demo.

