# Runtime, Tool Registry, and Human Gates

## Purpose

This document defines how agents should share common infrastructure without forcing all agents into the same runtime.

## Principle

Use one common agent shell with multiple runtime backends.

```text
Base Agent
  -> descriptor
  -> instructions
  -> input contract
  -> output contract
  -> runtime
  -> tools
  -> budget policy
  -> approval policy
```

## Runtime types

### DeterministicRuntime

Used for simple state transformations and schema validation.

Examples:

- basic classification;
- file path collection;
- artifact indexing;
- status updates.

### StructuredLLMRuntime

Used when an agent needs reasoning but no tools.

Examples:

- BA requirements generation;
- PM sprint generation;
- Architect planning;
- Handoff summary.

### LangChainExecutorRuntime

Used when an agent needs autonomous tool selection inside hard boundaries.

Examples:

- Team Lead Agent;
- Head Agent;
- future Architect with repo inspection tools.

### CodexCliRuntime

Used when an agent needs to modify code, run project commands, or inspect generated code deeply.

Examples:

- Fullstack Agent;
- QA Agent when it uses Codex for evidence generation;
- Deployment Agent when it uses Codex to reason about deployment topology;
- Handoff Agent if it generates polished reports from many files.

### HumanApprovalRuntime

Used for pause/resume gates.

Examples:

- deployment approval;
- continue to next sprint approval;
- unresolved blocker resolution.

## Tool registry

Add a platform-level registry so tools can be reused by multiple agents safely.

Suggested location:

```text
src/agentic_company/platform/tool_registry.py
```

Tool definition:

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    allowed_agents: list[str]
    requires_approval: bool
    risk_level: Literal["low", "medium", "high", "destructive"]
```

Example tools:

| Tool | Allowed agents | Approval | Risk |
|---|---|---:|---|
| `read_artifact` | all | no | low |
| `write_artifact` | all delivery agents | no | low |
| `read_sprint_plan` | team-lead, pm, head | no | low |
| `call_fullstack_agent` | team-lead, head | no | medium |
| `call_qa_agent` | team-lead, head | no | medium |
| `call_deployment_agent` | team-lead, head | yes | high |
| `call_handoff_agent` | team-lead, head | no | low |
| `create_escalation` | all | no | medium |
| `pause_run` | head, team-lead | no | medium |
| `deploy_azure` | deployment-agent | yes | high |
| `delete_cloud_resource` | deployment-agent | yes | destructive |

## Approval gates

Recommended gate model:

```python
class HumanGate(TypedDict):
    gate_id: str
    run_id: str
    gate_type: str
    reason: str
    requested_by_agent: str
    status: Literal["pending", "approved", "rejected", "expired"]
    evidence_paths: list[str]
```

Required gate types:

```text
sprint_plan_approval
continue_to_next_sprint
deployment_approval
unresolved_blocker
scope_change_request
```

## Pause / resume states

Run states:

```text
running
paused_waiting_for_human
blocked
cancelled
failed
completed
```

Events:

```text
HumanApprovalRequested
HumanApproved
HumanRejected
RunPaused
RunResumed
RunCancelled
EscalationCreated
```

## Console requirements

Console should show:

- active run;
- current sprint;
- current feature;
- active agent;
- pending human gates;
- blockers;
- approve/reject buttons;
- human input text box;
- evidence links.

## Cost controls

Do not run powerful agent executors for every trivial decision.

Recommended model:

```text
Rules first -> cheap structured LLM if uncertain -> full agent executor only when action is needed.
```

Team Lead can be autonomous, but each loop should have:

- max iterations;
- max repair attempts;
- max tool calls;
- timeout;
- structured final decision.

## Runtime acceptance

This layer is complete when:

- agents can declare allowed tools;
- risky tools require approval;
- Team Lead can pause for human approval;
- failed runs can be inspected through events/artifacts;
- new agents can reuse runtime and tool registry patterns.
