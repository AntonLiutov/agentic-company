# 02 Product And Business Analysis

## Product Owner Output

### Product Name Candidates

- Agentic Delivery OS
- AI Project Factory
- Agentic Company
- MVP Assembly Line
- Autonomous Delivery Team

Recommended working name:

> Agentic Delivery OS

This name is broad enough for enterprise positioning and specific enough to describe orchestration, not just chat.

## Product Vision

Agentic Delivery OS turns a raw business idea into a structured software delivery package and, in
the current PoC, a working generated Streamlit MVP that can be QA-tested, Docker-tested, deployed to
Azure Container Apps, smoke-tested at its public URL, and handed off. It coordinates specialized
agents for intake, product scope, architecture, implementation, QA, deployment, and handoff.

## Core Promise

For a founder, product manager, or innovation team:

> Give us the messy idea. The agent team turns it into a staffed plan, generated MVP, QA
> evidence, deployment summary, and handoff package.

## Business Analyst Output

### Problem

Teams lose time before the first line of useful code:

- Requirements are vague.
- Product scope is unclear.
- Architecture decisions are implicit.
- Handoffs between product, engineering, QA, and documentation are inconsistent.
- AI coding tools can generate code, but they often lack business context and delivery structure.

### Opportunity

Agentic systems can become more valuable when they operate like a delivery organization:

- Specialized roles
- Explicit artifacts
- Traceable decisions
- Repeatable workflows
- Real execution through coding agents

### Differentiator

Most AI coding demos start with a prompt and jump directly into code. This project adds the missing company layer:

- Intake before implementation
- Staffing before execution
- Architecture before code
- QA and handoff after generation
- Run logs for trust and observability

## Personas

### Founder

Needs:

- Move from idea to demo quickly
- Understand scope and cost
- Avoid overbuilding
- Show progress to investors or customers

Pain points:

- Unclear technical path
- Too much time lost deciding what to build first
- Hard to evaluate AI-generated output

### Product Manager

Needs:

- Convert vague requests into actionable scope
- Keep non-goals visible
- Align stakeholders around acceptance criteria

Pain points:

- Requirements drift
- Missing handoffs
- No reusable process across projects

### Engineering Lead

Needs:

- Clear implementation brief
- Reasonable architecture
- Constraints and risks surfaced early
- Generated work that can be reviewed

Pain points:

- Prompt-to-code systems create work without enough context
- QA and documentation are afterthoughts
- Tooling gets tied to one provider too early

## MVP Requirements

### Functional Requirements

- User can submit a requirements document or form.
- System runs the planning pipeline.
- System generates structured artifacts.
- System shows each agent step and output.
- System stores each run locally.
- User can copy or download the implementation brief.
- User can trigger a Codex execution agent for the first generated-project path.
- System creates a starter project folder for the current Streamlit LLM chat archetype.
- System can run QA against the generated project locally, in Docker, and in a browser.
- System can deploy the generated project to Azure Container Apps after explicit confirmation.

### Non-Functional Requirements

- Local-first development experience
- Clear logs and event trace
- Simple dependency footprint
- Docker is required for the generated-project QA/deployment path, but not for planning-only use.
- Provider-neutral architecture
- Easy to demo in 3-5 minutes

### Non-Goals For The First Weekend

- Full autonomous multi-agent concurrency
- Production authentication
- Billing
- Enterprise permissions
- Long-term memory
- Production-grade deployment automation beyond the current Azure dev reuse runner
- LangGraph or LangChain orchestration
- Figma integration

## Product Scope

### Weekend MVP

The original weekend MVP is implemented:

- A web interface for requirements input
- The deterministic planning pipeline running behind it
- Artifact preview for each step
- Event timeline
- Clear "ready for execution" implementation brief

### Hackathon MVP

The current PoC already includes:

- Codex runner wrapper
- Project folder generation
- Real file changes by the Fullstack Engineer Agent
- QA Agent report with command, Docker, browser, screenshot, and transcript evidence
- Azure deployment of generated projects after explicit confirmation
- Post-deployment browser QA

The hackathon MVP still needs:

- Hosted demo
- Demo video and slides
- Cleaner artifact grouping and demo narrative
- Automatic Engineer <-> QA repair loop

## Business Value Story

Agentic Delivery OS reduces the gap between idea and execution. It helps teams move faster while preserving the structure that real software delivery needs: requirements, scope, architecture, implementation, QA, and handoff.

The investor version:

> This is not another AI coding assistant. It is a repeatable operating layer for AI-native software delivery.
