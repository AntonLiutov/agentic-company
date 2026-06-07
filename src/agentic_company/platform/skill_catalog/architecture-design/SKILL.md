---
name: architecture-design
description: Convert requirements into a practical technical approach. Use for Solution Architect work, system shape, data model, integration boundaries, deployment constraints, and technical risks. Do not use for sprint sequencing, coding, QA execution, or release reporting.
---

# Architecture Design

## Purpose

Shape the technical path for the smallest reliable product slice without over-engineering the demo.

## Boundaries

- Owns app architecture, integration boundaries, data persistence choices, deployment shape, and technical risks.
- Does not implement code, run browser QA, or decide sprint order beyond dependency notes.
- Escalates when requested infrastructure, secrets, or compliance constraints require approval.

## Inputs

- Requirements brief and acceptance criteria.
- Existing project constraints and generated-app target context.
- Provider, deployment, persistence, and integration assumptions.

## Workflow

1. Read the requirements brief and preserve business intent.
2. Choose the simplest architecture that can satisfy the acceptance criteria.
3. Identify data model, UI surfaces, backend/API needs, and deployment path.
4. Mark integration/secrets/cloud assumptions explicitly.
5. Produce risks and validation points for QA and deployment.
6. Return an architecture report with artifact references.

## Output Contract

- Architecture report artifact.
- Technical approach, key components, data/persistence notes, risks, and validation points.
- Dashboard-safe comment for work tracking systems.

## Quality Rules

- Prefer existing project patterns over new frameworks.
- Avoid speculative enterprise scope unless the request requires it.
- Make deployment and runtime assumptions explicit.

## Failure And Repair

- Retry when architecture misses required product behavior.
- Block when key infrastructure or secrets are missing and no safe assumption exists.
- Human approval is required for high-risk deployment/security decisions.

## Examples

### Good invocation

Input: Requirements for a shared task board.
Output: Simple web app architecture with persistent task storage and deployment risks.

### Bad invocation

Input: Small prototype requirements.
Output: Microservices architecture with unnecessary queues and enterprise SSO.
