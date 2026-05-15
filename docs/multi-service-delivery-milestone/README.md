# Multi-Service Delivery Milestone

This folder defines the next major PoC milestone after the Stage 6 platform
structure cleanup.

The previous milestone proved one narrow vertical slice:

```text
requirements
  -> planning
  -> fullstack implementation
  -> QA
  -> Azure deployment
  -> deployment smoke validation
  -> handoff
```

That slice works for a simple generated Streamlit chat app. The next milestone
must prove something more important: the platform can coordinate a realistic
software delivery workflow with more than one feature, more than one generated
service, repair loops, topology-aware deployment, and business-quality handoff
evidence.

## Milestone Name

**MSD-001: Multi-Service, Multi-Feature Agentic Delivery**

## Milestone Goal

Enable the platform to take a slightly richer product request, break it into
multiple feature work items, generate a small multi-service application, validate
it with an agent-created executable QA strategy and generic test tools, deploy it with
stable dev resources, prove the deployed dev runtime with deployment-owned smoke
evidence, and produce a polished handoff report for each delivered feature/release.

The functionality can stay intentionally simple. The workflow must become more
real.

## Why This Milestone Matters

The end goal is an agentic company platform with many specialist agents. Jumping
directly to 20 sophisticated agents would create ceremony without proof. This
milestone is the next useful proof point because it forces the current core
agents to become more self-sufficient:

- Planning must handle more than one work item.
- Fullstack must generate a consistent multi-service project.
- QA must reason from requirements and generated topology, not only run fixed
  smoke checks.
- Deployment must inspect topology and update stable resources instead of
  assuming one container.
- Handoff must become a real delivery package, not only a short local summary.

## Documents

- [01 Milestone Charter](01-milestone-charter.md)
- [02 Epics, Sprints, And Tasks](02-epics-sprints-and-tasks.md)
- [03 Agent Workflows And Contracts](03-agent-workflows-and-contracts.md)
- [04 Acceptance, QA, And Handoff](04-acceptance-qa-and-handoff.md)

## Implementation Rule

Do not optimize for file count. Optimize for ownership.

```text
Company graph owns flow.
Agents own specialist decisions.
Integrations own reusable tools.
Platform owns shared state, events, artifacts, and security.
Console owns user interaction and run visibility.
```

Every task in this milestone should make one of those boundaries stronger.
