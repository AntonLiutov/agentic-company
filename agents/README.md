# Agents

This folder contains the canonical company agent registry.

## Structure

Each agent lives in its own folder and has two files:

- `README.md` - human-readable role specification
- `agent.yaml` - machine-readable configuration for future orchestration code

## Current Roster

- `intake-agent/`
- `team-assembler-agent/`
- `sales-discovery-agent/`
- `product-manager-agent/`
- `business-analyst-agent/`
- `project-delivery-manager-agent/`
- `ux-product-designer-agent/`
- `solution-architect-agent/`
- `tech-lead-agent/`
- `frontend-engineer-agent/`
- `backend-engineer-agent/`
- `fullstack-engineer-agent/`
- `ai-llm-engineer-agent/`
- `data-engineer-agent/`
- `devops-platform-agent/`
- `qa-agent/`
- `security-review-agent/`
- `documentation-handoff-agent/`
- `support-customer-success-agent/`
- `knowledge-memory-agent/`

## Authoring Rules

- Keep `README.md` clear enough for a human founder, operator, or teammate to review
- Keep `agent.yaml` stable and structured enough for future code loading
- Update both files together when an agent changes
- Prefer explicit handoff contracts over vague behavioral descriptions

## Recommended Authoring Flow

1. Update the human-readable role description first
2. Mirror the operational details into `agent.yaml`
3. Validate that responsibilities, inputs, outputs, and escalation rules match in both files
