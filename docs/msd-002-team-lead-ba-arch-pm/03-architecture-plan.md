# Solution Architecture Plan

## Role

**Solution Architect Agent**

## Mission

Translate BA requirements into a technical delivery architecture for the agentic platform milestone. The Architect does not implement code. It defines boundaries, contracts, state, runtime approach, and constraints for PM, Team Lead, Fullstack, QA, Deployment, and Handoff.

## Architecture decision summary

MSD-002 should avoid free-form agent rooms and implement a structured sprint orchestration model.

Recommended architecture:

```text
Top-level delivery graph
  -> BA Agent
  -> Architect Agent
  -> PM Agent
  -> Team Lead Agent per sprint
       -> Fullstack Agent per feature
       -> QA Agent per feature
       -> repair loop
       -> Deployment Agent after sprint pass
       -> Handoff Agent sprint report
  -> Handoff Agent final report
```

## Key technical decisions

### ADR-001 - Keep Head Agent deterministic for MSD-002

Decision: The top-level Head / Delivery Coordinator remains mostly deterministic.

Reason:

- reduces risk;
- makes runs explainable;
- avoids costly routing loops;
- allows Team Lead to become autonomous first.

Consequence:

- Head runs the planned sequence.
- Smart Head Agent becomes a later milestone.

### ADR-002 - Introduce Team Lead as the first autonomous coordinator

Decision: The Team Lead Agent can be implemented as a LangChain/LangGraph executor with tools, but within hard boundaries.

Allowed Team Lead decisions:

- choose next feature from sprint plan;
- assign feature to Fullstack;
- request QA;
- route QA failure back to Fullstack;
- escalate after max attempts;
- request deployment after all sprint features pass;
- request sprint handoff report.

Forbidden Team Lead decisions:

- changing product scope;
- changing sprint contents without PM/Head approval;
- deploying without human approval;
- using destructive tools;
- calling arbitrary shell commands outside allowed tools.

### ADR-003 - Use structured artifacts, not free-form chat, as the primary contract

Decision: Each agent writes explicit artifacts and structured results.

Reason:

- QA can reference evidence;
- Handoff can summarize reliably;
- console can display progress;
- runs can be resumed/debugged.

### ADR-004 - Add human gates before risky steps

Required gates:

- approve sprint plan before execution;
- approve deployment before Azure actions;
- resolve blocker when agents cannot proceed;
- optional approval before moving from one sprint to the next.

### ADR-005 - Make runtime replaceable

Agents should not be tied to one runtime. Use a runtime abstraction:

```text
DeterministicRuntime
StructuredLLMRuntime
LangChainExecutorRuntime
CodexCliRuntime
HumanApprovalRuntime
```

## Proposed module layout

```text
src/agentic_company/
  agents/
    business_analysis/
      agent.py
      graph.py
      models.py
    architecture/
      agent.py
      graph.py
      models.py
    project_manager/
      agent.py
      graph.py
      models.py
    team_lead/
      agent.py
      graph.py
      models.py
      sprint_runner.py
      feature_loop.py
  platform/
    sprints.py
    tasks.py
    escalations.py
    human_gates.py
    tool_registry.py
    runtime.py
  orchestration/
    graphs/
      delivery.py
      nodes.py
```

## State model additions

### SprintPlan

```python
class SprintPlan(TypedDict):
    sprint_id: str
    title: str
    goal: str
    features: list[FeatureTask]
    exit_criteria: list[str]
    deployment_policy: str
    is_final_sprint: bool
```

### FeatureTask

```python
class FeatureTask(TypedDict):
    feature_id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    dependencies: list[str]
    implementation_notes: list[str]
    qa_notes: list[str]
    deployment_notes: list[str]
    status: str
```

### TeamLeadResult

```python
class TeamLeadResult(TypedDict):
    sprint_id: str
    status: Literal["passed", "failed", "blocked", "escalated"]
    completed_features: list[str]
    failed_features: list[str]
    blockers: list[str]
    artifacts: list[ArtifactRef]
    next_recommended_action: str
```

### Escalation

```python
class Escalation(TypedDict):
    reason: str
    owner: Literal["human", "fullstack", "qa", "deployment", "pm", "architect"]
    blocking: bool
    evidence_paths: list[str]
    suggested_resolution: str
```

## Team Lead internal graph

```text
team_lead_start
  -> select_next_feature
  -> fullstack
  -> qa
  -> route_after_qa
       passed + more_features -> select_next_feature
       passed + sprint_done -> deployment_gate
       failed + attempts_left -> fullstack_repair
       failed + attempts_exhausted -> escalation
  -> deployment
  -> sprint_handoff
  -> team_lead_complete
```

## Top-level graph after MSD-002

```text
START
  -> business_analysis
  -> architecture
  -> project_management
  -> for_each_sprint(team_lead)
  -> final_handoff
  -> END
```

Initial implementation can keep `for_each_sprint` as a deterministic loop in the Head/Delivery Coordinator.

## Tool access policy

| Agent | Tools |
|---|---|
| BA | read/write artifacts, structured LLM |
| Architect | read artifacts, inspect repo docs, write architecture artifacts |
| PM | read BA/Architect artifacts, write release/sprint plan |
| Team Lead | read sprint plan, call agents, write sprint result, request gates |
| Fullstack | Codex execution in generated project |
| QA | tests, browser checks, Docker checks, evidence writing |
| Deployment | Docker/Azure tools after approval |
| Handoff | read evidence, write reports |

## Architecture acceptance

Architecture work is complete when:

- module boundaries are clear;
- state contracts are defined;
- Team Lead execution loop is explicit;
- runtime abstraction is documented;
- human gates are defined;
- PM/BA/Architect outputs are consumable by downstream agents.
