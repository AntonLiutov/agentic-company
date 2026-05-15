# 01 Intake Brief

## Intake Agent Summary

The user is enrolled in the Milan AI Week AI Agent Olympics Hackathon and wants to turn `agentic-company` into a credible, impressive hackathon project. The desired product is an agentic delivery system that can take rough requirements, process them through specialized agents, and move toward real project execution. The first working slice already exists as a deterministic planning pipeline for a `web-app-mvp` workflow.

## Raw Need

Create an agentic system that demonstrates how a small team of AI agents can turn a raw business idea into a working MVP faster than a normal team could start from scratch.

The project should feel real enough for a hackathon stage:

- Clear enterprise use case
- Visible multi-agent workflow
- Real artifacts generated during a run
- A path from planning into execution
- A demo that is understandable to non-technical people
- A pitch that can interest builders, companies, and investors

## Hackathon Context

Event:

- AI Agent Olympics Hackathon at Milan AI Week 2026
- Online build phase: May 13-19, 2026
- Selected on-site build day: May 19, 2026
- Demo showcase and awards: May 20, 2026
- Venue: Fiera Milano, Rho, Milan, Italy

Relevant judging dimensions:

- Application of technology
- Presentation
- Business value
- Originality

Best-fit tracks:

- Agentic Workflows
- Collaborative Systems
- Enterprise Utility
- Vultr web-based enterprise agent angle

## Product Hypothesis

Enterprise teams do not only need chatbots. They need structured AI systems that can take messy requests and turn them into reliable execution plans, working code, QA notes, and handoff packages.

Our system can become:

> A project delivery autopilot for internal tools, prototypes, and MVPs.

## Target Users

Primary:

- Startup founders
- Product managers
- Engineering leads
- Innovation teams
- Agencies or consultancies building many small client projects

Secondary:

- Hackathon teams
- Solo builders
- Enterprise teams evaluating AI delivery automation
- Investors looking for repeatable AI-native service businesses

## MVP Input

The first input should stay simple:

- Project name
- Business goal
- Target user
- Core features
- Required API keys or integrations
- Preferred stack
- Non-goals
- Acceptance criteria

## MVP Output

The first output should be both human-readable and machine-readable:

- Intake brief
- Project classification
- Staffing decision
- Workflow plan
- Implementation brief
- Event trace
- Later: generated project files
- Later: QA report
- Later: handoff summary

## Constraints

- Keep the first version simple and fast.
- Do not require Docker Compose for the first local MVP.
- Use `gpt-4o-mini` as the default model example for generic LLM config.
- Keep GPT-5.5/Codex as the first serious execution worker direction.
- Do not adopt LangChain or LangGraph until the workflow becomes dynamic enough to justify it.
- Keep agent handoffs explicit through artifacts.

## Success Criteria

For the next development milestone, success means:

- A user can submit requirements through a basic interface.
- The system runs the planning pipeline.
- The user can inspect artifacts and events.
- The implementation brief is clear enough for a Codex worker agent.
- The architecture leaves room for future Claude, Gemini, Figma, and other providers.

For the hackathon MVP, success means:

- The system creates or modifies a real starter project.
- The demo shows multiple agent roles doing differentiated work.
- The product story is understandable in under 60 seconds.
- The GitHub repo is public, documented, and runnable.
- The demo app is hosted.
