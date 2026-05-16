# MSD-002 Task Breakdown

## Epic A - Team Lead Sprint Orchestration

### A1 - Add sprint platform models

Files likely affected:

```text
src/agentic_company/platform/sprints.py
src/agentic_company/platform/escalations.py
src/agentic_company/platform/human_gates.py
tests/unit/platform/test_sprints.py
```

Tasks:

- Add `SprintPlan`.
- Add `FeatureTask`.
- Add `SprintExecutionState`.
- Add `TeamLeadResult`.
- Add `Escalation`.
- Add validation helpers.
- Add tests.

### A2 - Add TeamLeadAgent skeleton

Files:

```text
src/agentic_company/agents/team_lead/__init__.py
src/agentic_company/agents/team_lead/agent.py
src/agentic_company/agents/team_lead/graph.py
src/agentic_company/agents/team_lead/models.py
```

Tasks:

- Add `AgentDescriptor`.
- Implement `run(state) -> state`.
- Add fake-runner injectable constructor parameters.
- Write initial sprint result artifact.
- Add import/registry integration if appropriate.

### A3 - Implement feature loop

Files:

```text
src/agentic_company/agents/team_lead/feature_loop.py
src/agentic_company/agents/team_lead/sprint_runner.py
tests/unit/agents/team_lead/test_feature_loop.py
```

Tasks:

- Select next incomplete feature.
- Call Fullstack for feature.
- Call QA for feature.
- Mark feature complete on QA pass.
- Continue until sprint done.

### A4 - Implement QA repair loop

Tasks:

- Parse QA result/fix request.
- Route Fullstack-owned failure back to Fullstack.
- Track repair attempts per feature.
- Block sprint after max attempts.
- Write escalation artifact.

### A5 - Deployment and sprint handoff

Tasks:

- Call Deployment only after all features pass.
- Respect deployment approval state.
- Call Handoff in `sprint_report` mode.
- Persist sprint report artifact path.
- Write final TeamLeadResult.

## Epic B - Project Manager Agent

### B1 - PM agent skeleton

Files:

```text
src/agentic_company/agents/project_manager/__init__.py
src/agentic_company/agents/project_manager/agent.py
src/agentic_company/agents/project_manager/models.py
src/agentic_company/agents/project_manager/release_planning.py
```

Tasks:

- Add PM descriptor.
- Add ReleasePlan model.
- Read BA/Architect artifacts.
- Write a bounded release plan and sprint/work board artifacts.

### B2 - PM release plan artifacts

Outputs:

```text
project-management/release-plan.json
project-management/release-plan.md
project-management/roadmap.csv
project-management/work-board.json
project-management/dod.md
```

Tasks:

- Generate feature IDs.
- Add dependencies.
- Add acceptance criteria.
- Add QA/deployment notes.

## Epic C - Solution Architect Agent

### C1 - Architect skeleton

Files:

```text
src/agentic_company/agents/architecture/__init__.py
src/agentic_company/agents/architecture/agent.py
src/agentic_company/agents/architecture/models.py
src/agentic_company/agents/architecture/planning.py
```

Tasks:

- Add Architect descriptor.
- Read requirements artifacts.
- Write architecture artifacts.
- Include implementation constraints.

### C2 - Architect artifacts

Outputs:

```text
architecture/architecture-plan.md
architecture/technical-decisions.md
architecture/risks.md
architecture/implementation-constraints.md
```

## Epic D - Business Analyst Agent

### D1 - BA skeleton

Files:

```text
src/agentic_company/agents/business_analysis/__init__.py
src/agentic_company/agents/business_analysis/agent.py
src/agentic_company/agents/business_analysis/models.py
src/agentic_company/agents/business_analysis/requirements.py
```

Tasks:

- Add BA descriptor.
- Read raw requirements.
- Write requirements artifacts.
- Include open questions and edge cases.

### D2 - BA artifacts

Outputs:

```text
business-analysis/requirements-spec.md
business-analysis/user-stories.json
business-analysis/acceptance-criteria.json
business-analysis/edge-cases.md
business-analysis/open-questions.md
```

## Epic E - Graph integration

Tasks:

- Add new graph node wrappers for BA, Architect, PM, Team Lead.
- Add optional graph mode for MSD-002.
- Keep existing graph behavior intact.
- Add integration test with fake agents.

Proposed graph:

```text
START
  -> head
  -> business_analysis
  -> architecture
  -> project_management
  -> team_lead
  -> delivery gates / handoff
  -> head completion
  -> END
```

Implementation should remain artifact-driven rather than fixed to a hard-coded
number of sprint nodes.

## Epic F - Console and human gates

Tasks:

- Show release plan.
- Show sprint list.
- Show current sprint.
- Show current feature.
- Show active agent.
- Show blockers/escalations.
- Show approval-required state.
- Link sprint reports and final report.

## Epic G - Documentation

Tasks:

- Update root README with MSD-002 summary.
- Add milestone README link.
- Document run command.
- Document limitations.
- Document next milestone.

## Suggested order for Codex

1. A1
2. A2
3. A3
4. A4
5. A5
6. B1/B2
7. C1/C2
8. D1/D2
9. E
10. F
11. G

## Cutline if time is short

Must-have:

- A1-A5.

Should-have:

- B1-B2.

Nice-to-have:

- C, D, E, F.

Do not sacrifice Team Lead stability for upstream agent polish.
