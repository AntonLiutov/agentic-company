# Team Lead Agent Contract

## Identity

```yaml
name: Team Lead Agent
family: delivery-execution
version: 0.1.0
status: implemented-poc
runtime: LangChain/LangGraph-capable agent executor with hard boundaries
```

## Mission

Execute approved sprint plans by coordinating Fullstack, QA, Deployment, and Handoff agents.

## Responsibilities

Team Lead owns:

- sprint execution;
- feature sequencing inside the sprint;
- assigning features to Fullstack;
- asking QA to validate each feature;
- routing QA failures back to Fullstack;
- escalating blockers;
- triggering deployment after sprint features pass;
- triggering sprint handoff report;
- producing structured sprint result.

Team Lead does not own:

- raw requirements discovery;
- product prioritization;
- architecture decisions;
- changing sprint scope;
- final business approval;
- destructive cloud operations.

## Input contract

Team Lead receives:

```json
{
  "run_id": "run-001",
  "sprint_plan": {
    "sprint_id": "S1",
    "title": "Team Lead execution foundation",
    "goal": "Execute features with Fullstack and QA repair loop.",
    "features": [],
    "exit_criteria": [],
    "deployment_policy": "deploy_after_sprint",
    "is_final_sprint": false
  },
  "available_agents": [
    "fullstack-agent",
    "qa-agent",
    "deployment-agent",
    "handoff-agent"
  ],
  "max_repair_attempts": 2,
  "human_gate_policy": {
    "deployment_requires_approval": true,
    "continue_to_next_sprint_requires_approval": false
  }
}
```

## Output contract

Team Lead writes:

```text
runs/<run-id>/upstream-planning/team-lead/
  response-*.json
  work-board snapshots or refs when produced
runs/<run-id>/handoff/sprints/<sprint-id>/
  09-handoff-summary.md
  release-report.html
  release-evidence.json
```

Final result:

```json
{
  "sprint_id": "S1",
  "status": "passed | failed | blocked | escalated",
  "completed_features": ["F1", "F2"],
  "failed_features": [],
  "blockers": [],
  "deployment_summary_path": "deployment/13-deployment-summary.md",
  "handoff_summary_path": "handoff/sprints/sprint-01/09-handoff-summary.md",
  "artifact_refs": ["handoff/sprints/sprint-01/09-handoff-summary.md"],
  "next_recommended_action": "continue_to_next_sprint"
}
```

## Internal execution algorithm

```text
1. Read sprint plan.
2. Validate that sprint has at least one feature.
3. For each feature in delivery order:
   3.1. Send feature package to Fullstack Agent.
   3.2. Wait for implementation result.
   3.3. Send feature and implementation evidence to QA Agent.
   3.4. If QA passes, mark feature completed.
   3.5. If QA fails and owner is Fullstack, send fix request to Fullstack.
   3.6. Retry until max repair attempts reached.
   3.7. If failure remains, block or escalate sprint.
4. When planned sprint work passes QA, decide the next gate from PM roadmap and current evidence.
5. If the sprint/release calls for deployment, call Deployment Agent.
6. If the sprint is local-only or deployment is complete/blocked with evidence, call Handoff Agent for a sprint-scoped handoff.
7. Return structured status and actual artifact refs to Head.
```

## LangChain / LangGraph use

Team Lead can be implemented as an autonomous agent executor, but it must operate inside a controlled graph/state machine.

Recommended pattern:

```text
LangGraph controls durable state and routing.
LangChain AgentExecutor is used inside Team Lead decision nodes.
Tools expose only safe actions.
Every decision writes structured output.
```

## Team Lead tools

| Tool | Description | Risk |
|---|---|---|
| `read_sprint_plan` | Read current sprint package | low |
| `get_feature_status` | Read feature execution state | low |
| `call_fullstack_agent` | Ask Fullstack to implement/repair feature | medium |
| `call_qa_agent` | Ask QA to validate feature | medium |
| `call_deployment_agent` | Request deployment after approval | high |
| `call_handoff_agent` | Request sprint or final report | low |
| `create_escalation` | Stop and request human/help | medium |
| `write_sprint_result` | Persist sprint result | low |

## Hard boundaries

Team Lead must not:

- edit generated project directly;
- modify product scope;
- skip QA;
- deploy without approval;
- run arbitrary shell commands;
- retry forever;
- hide blockers;
- delete artifacts.

## Repair loop policy

Default:

```text
max_repair_attempts = 3
```

If QA fails:

- Team Lead reads QA failure owner.
- If owner is Fullstack, route fix request to Fullstack.
- If owner is Deployment, defer to deployment stage or block if deployment already started.
- If owner is Human, create escalation.
- If owner is ambiguous, ask Head/PM or human.

## Sprint deployment policy

Supported values:

```text
none
manual_after_sprint
auto_dev_after_sprint_with_approval
final_only
```

MSD-002 default:

```text
auto_dev_after_sprint_with_approval
```

## Handoff modes

Team Lead calls Handoff in one of two modes:

```text
sprint_report
final_project_report
```

Sprint handoff artifacts should be scoped under:

```text
runs/<run-id>/handoff/sprints/<sprint-id>/
```

Project/final handoff artifacts should be scoped under:

```text
runs/<run-id>/handoff/project/
```

Sprint report should include:

- sprint goal;
- delivered features;
- QA summary;
- deployment summary;
- evidence links;
- known limitations;
- next recommended action.

## Tests

Required tests:

1. Team Lead runs one passing feature.
2. Team Lead runs two passing features sequentially.
3. QA failure routes back to Fullstack.
4. Max repair attempts blocks sprint.
5. Deployment does not run before all features pass.
6. Handoff runs after deployment.
7. TeamLeadResult contains correct status and artifact refs.
8. Human approval gate pauses deployment.
