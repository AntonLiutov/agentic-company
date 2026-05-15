# 10 Agent Deep Dive

This document expands the company agent roster into practical implementation guidance. Each agent is described by responsibility, interaction model, artifacts, first implementation, and future maturity path.

## Agent Summary Matrix

| Agent | Primary Job | First Implementation | Future Upgrade |
| --- | --- | --- | --- |
| Intake Agent | Convert raw input into a normalized brief | L0 parser + L1 cleanup | Voice intake, document upload, clarification chat |
| Team Assembler Agent | Select the smallest useful team | L0 rules | L1 rationale, L3 cost/risk tools |
| Sales / Discovery Agent | Clarify business opportunity | L1 interview script | Voice calls, CRM integration |
| Product Manager Agent | Define MVP scope and success criteria | L1 structured scope | Human approval gates, roadmap tools |
| Business Analyst Agent | Detail requirements and edge cases | L1 requirements expansion | Document analysis, stakeholder Q&A |
| Project / Delivery Manager Agent | Plan phases, dependencies, risks | L0 templates + L1 synthesis | Timeline tools, issue creation |
| UX / Product Designer Agent | Define flows and UI direction | L1 design brief | Claude + Figma, screenshot review |
| Solution Architect Agent | Define system shape and tradeoffs | L1 architecture brief | Tool-assisted repo analysis |
| Tech Lead Agent | Convert architecture into implementation tasks | L1 implementation brief | Codex-assisted repo planning |
| Frontend Engineer Agent | Build UI and client behavior | L6 Codex | Browser automation, visual tests |
| Backend Engineer Agent | Build APIs, data, services | L6 Codex | Database and API tools |
| Fullstack Engineer Agent | Build compact end-to-end MVPs | L6 Codex | Multi-step code/test loop |
| AI / LLM Engineer Agent | Design model, prompt, retrieval, evals | L1/L3 | Eval harnesses, provider comparison |
| Data Engineer Agent | Design data flows and quality controls | L1 | Data profiling and pipeline tools |
| DevOps / Platform Agent | Prepare deployment and operations | L2/L3 Azure Container Apps runner for generated projects | CI/CD, rollback, multi-cloud APIs |
| QA Agent | Validate readiness | L3 tool checks with Docker and Playwright | Codex repair/review, broader test generation |
| Security Review Agent | Identify security and privacy risks | L1 checklist | Static scanning, dependency review |
| Documentation / Handoff Agent | Package final delivery | L1 synthesis | Repo editing through Codex |
| Support / Customer Success Agent | Support adoption after handoff | L1 FAQ | Ticket/chat integrations |
| Knowledge / Memory Agent | Preserve reusable knowledge | L0 file index | Vector memory, decision search |

## Intake Agent

Main responsibilities:

- Accept raw ideas, notes, voice transcripts, pasted requirements, or uploaded documents.
- Normalize messy input into a structured project brief.
- Identify missing information and ask clarifying questions.
- Separate goals, users, features, constraints, non-goals, configuration, and acceptance criteria.

User interaction:

- Text area in the planning console.
- Upload later for docs or PDFs.
- Voice later: record or transcribe a founder explaining the idea.
- Chat refinement later: the agent asks two or three focused questions.

Artifacts:

- `01-intake-brief.json`
- `open-questions.md`
- `source-transcript.md` for voice intake later

First version:

- L0 hardcoded parser for the sample format.
- L1 simple LLM cleanup for unstructured text.

Future versions:

- L2 agent that asks clarifying questions.
- L3 tool executor for document extraction.
- Voice input through transcription before intake.

## Team Assembler Agent

Main responsibilities:

- Choose the smallest effective team for the project.
- Explain why each role is included.
- Mark optional roles when risk or ambiguity increases.
- Avoid overstaffing simple MVPs.

User interaction:

- User reviews selected team.
- User can add or remove optional roles.
- Later, user can choose "fast", "balanced", or "thorough" staffing mode.

Artifacts:

- `03-staffing-decision.json`
- `staffing-rationale.md`

First version:

- L0 rules based on project type, complexity, and delivery mode.

Future versions:

- L1 rationale generation.
- L3 cost/time estimator.
- L5 graph node that branches workflow based on selected team.

## Sales / Discovery Agent

Main responsibilities:

- Clarify business opportunity and buyer context.
- Identify budget, urgency, stakeholder, and desired business outcome.
- Convert vague demand into discovery notes.

User interaction:

- Voice or chat interview.
- Short guided form.
- Later CRM handoff.

Artifacts:

- `discovery-notes.md`
- `opportunity-summary.md`
- `stakeholder-map.md`

First version:

- L1 simple LLM interview summary.

Future versions:

- Voice-first discovery call.
- CRM integration.
- Proposal draft generation.

## Product Manager Agent

Main responsibilities:

- Define MVP scope.
- Clarify user stories, non-goals, and acceptance criteria.
- Protect against overbuilding.
- Turn intake into product direction.

User interaction:

- Artifact review and approval.
- Scope adjustment controls.
- Clarifying chat for priorities.

Artifacts:

- `02-product-scope.md`
- `user-stories.md`
- `acceptance-criteria.md`

First version:

- L1 structured LLM call producing scope from intake.

Future versions:

- Human approval gates.
- Roadmap generation.
- Issue creation for selected scope.

## Business Analyst Agent

Main responsibilities:

- Expand requirements into detailed behavior.
- Identify edge cases and assumptions.
- Clarify integrations, data, permissions, and operational constraints.
- Translate business wording into implementation-ready requirements.

User interaction:

- Requirements review.
- Clarifying questions.
- Document upload analysis.

Artifacts:

- `03-requirements-analysis.md`
- `edge-cases.md`
- `assumptions.md`

First version:

- L1 simple LLM requirements expansion.

Future versions:

- L3 document analysis.
- Stakeholder Q&A loop.
- Schema validation of requirements.

## Project / Delivery Manager Agent

Main responsibilities:

- Convert scope into phases and tasks.
- Track dependencies, risks, and delivery sequence.
- Decide what can be parallelized.
- Prepare sprint plan and definition of done.

User interaction:

- Run timeline view.
- Risk review.
- Task approval before execution.

Artifacts:

- `06-delivery-plan.md`
- `task-breakdown.md`
- `risk-register.md`

First version:

- L0 template plus L1 synthesis.

Future versions:

- Issue creation in GitHub or Linear.
- Progress tracking.
- Human approval checkpoints.

## UX / Product Designer Agent

Main responsibilities:

- Define user flows and screen structure.
- Produce UX notes for the engineer.
- Identify usability risks and empty/error states.
- Later create or update Figma designs.

User interaction:

- User selects visual direction.
- User reviews wireframe/design brief.
- Later Figma file is created or updated.

Artifacts:

- `07-design-brief.md`
- `user-flow.md`
- `screen-inventory.md`
- Later `figma-link.md`

First version:

- L1 text design brief.

Future versions:

- L7 Claude + Figma design agent.
- Screenshot review.
- Figma component mapping.
- Accessibility review.

Notes:

- This role probably should not be Codex-first.
- The natural advanced path is Claude for visual/product reasoning plus Figma tools for design artifacts.

## Solution Architect Agent

Main responsibilities:

- Choose system architecture.
- Identify core components, boundaries, data flow, and integrations.
- Record tradeoffs and risks.
- Keep execution simple for the MVP.

User interaction:

- Architecture review.
- Stack confirmation.
- Approval before implementation.

Artifacts:

- `05-architecture-plan.md`
- `architecture-decisions.md`
- `integration-notes.md`

First version:

- L1 architecture brief.

Future versions:

- L3 tool-assisted docs lookup.
- L6 repo-aware Codex analysis for existing projects.
- ADR generation.

## Tech Lead Agent

Main responsibilities:

- Translate architecture into implementation tasks.
- Define file structure, boundaries, and coding constraints.
- Prepare the engineering prompt for Codex.
- Keep the build small and reviewable.

User interaction:

- Technical plan review.
- "Approve execution" gate before Codex writes files.

Artifacts:

- `05-implementation-brief.md`
- `06-execution-request.json`
- `engineering-task-list.md`

First version:

- L1 implementation brief generation.

Future versions:

- L6 Codex-assisted repo planning.
- Test strategy generation.
- Work splitting across engineering agents.

## Frontend Engineer Agent

Main responsibilities:

- Build user interfaces.
- Implement client-side state and interactions.
- Respect UX/design brief.
- Handle responsive behavior and empty/error/loading states.

User interaction:

- User reviews generated UI.
- Later screenshot or browser preview.

Artifacts:

- Frontend code files
- `frontend-summary.md`
- Later screenshots and visual QA output

First version:

- L6 Codex coding agent when frontend work is isolated.

Future versions:

- Browser automation.
- Visual regression checks.
- Design-to-code from Figma.

## Backend Engineer Agent

Main responsibilities:

- Build APIs, services, persistence, and business logic.
- Handle configuration and integration boundaries.
- Keep secrets and environment variables safe.

User interaction:

- API plan review.
- Environment setup review.

Artifacts:

- Backend code files
- `api-notes.md`
- `env-vars.md`

First version:

- L6 Codex when backend work exists.

Future versions:

- Database migration tools.
- API contract validation.
- Integration test generation.

## Fullstack Engineer Agent

Main responsibilities:

- Build compact end-to-end MVPs.
- Own generated application implementation.
- Create files from implementation brief.
- Write setup notes and keep scope tight.

User interaction:

- User approves execution request.
- User reviews generated project summary.

Artifacts:

- Generated project folder
- `07-execution-summary.md`
- App README

First version:

- L6 Codex agent.

Future versions:

- Multi-step code/test/fix loop.
- Split frontend/backend subtasks.
- PR creation.

## AI / LLM Engineer Agent

Main responsibilities:

- Choose model strategy.
- Design prompts, tool calls, retrieval, and evaluation.
- Define provider fallback strategy.
- Keep model use measurable.

User interaction:

- Model/provider selection.
- Prompt review for sensitive workflows.

Artifacts:

- `llm-strategy.md`
- `prompt-plan.md`
- `eval-plan.md`

First version:

- L1 model/prompt recommendation.

Future versions:

- L3 provider comparison.
- Eval harness.
- Retrieval pipeline design.

## Data Engineer Agent

Main responsibilities:

- Identify data sources and flows.
- Define data quality expectations.
- Plan storage and transformation.
- Surface privacy and retention risks.

User interaction:

- Data source review.
- Data handling approval.

Artifacts:

- `data-plan.md`
- `data-contracts.md`
- `quality-checks.md`

First version:

- L1 data plan for data-heavy projects only.

Future versions:

- Profiling tools.
- Pipeline generation.
- Data validation.

## DevOps / Platform Agent

Main responsibilities:

- Prepare deployment path and operational setup.
- Define environment variables and secrets.
- Add CI/CD, Docker, or hosting only when needed.
- Keep local MVPs simple.

User interaction:

- Deployment target selection.
- Secret/config review.

Artifacts:

- `deployment-plan.md`
- `operations-notes.md`
- CI or deployment config files

First version:

- L0/L1 deployment planning plus L2/L3 Azure Container Apps execution for generated Dockerized
  projects.

Future versions:

- GitHub Actions updates.
- Azure DevOps or GitHub Actions deployment modes.
- Rollback and teardown actions.
- Vultr or other cloud deployment integration.

Notes:

- The platform itself remains local in the current PoC.
- The implemented deployment path targets generated client projects.
- The current Azure mode intentionally reuses stable dev resources for speed.

## QA Agent

Main responsibilities:

- Validate output against acceptance criteria.
- Execute local automated checks and preserve evidence.
- Identify bugs, missing states, and demo risks.
- Decide whether a run is handoff-ready.

User interaction:

- QA report review.
- User can accept risk or send back to engineering.

Artifacts:

- `08-qa-report.md`
- `qa/results.json`
- `qa/commands.log`

First version:

- L3 tool runner with expected-file checks, secret scan, README checks, dependency sync, Python
  compile, Streamlit AppTest, Docker Compose config, Docker runtime E2E, Playwright live chat E2E,
  screenshots, transcripts, Docker build summaries, and structured reports.

Future versions:

- L6 Codex repair and review.
- Automated test generation.
- Accessibility, performance, security, and broader project-type checks.

## Security Review Agent

Main responsibilities:

- Identify security, privacy, and access risks.
- Review secrets handling.
- Flag risky dependencies or deployment choices.
- Keep first MVPs honest about limitations.

User interaction:

- Security risk approval.
- Secrets/config review.

Artifacts:

- `security-review.md`
- `privacy-notes.md`
- `risk-register.md`

First version:

- L1 security checklist.

Future versions:

- Dependency scanning.
- Static analysis.
- Threat modeling for enterprise projects.

## Documentation / Handoff Agent

Main responsibilities:

- Package the final delivery.
- Explain setup, usage, limitations, and next steps.
- Convert generated artifacts into a handoff summary.

User interaction:

- User reviews final handoff.
- User can request shorter or more client-facing version.

Artifacts:

- `09-handoff-summary.md`
- README updates
- `next-steps.md`

First version:

- L0/L1 synthesis after successful deployment, including public URL, configuration, QA/deployment
  evidence pointers, and next steps.

Future versions:

- L6 Codex edits to generated project README.
- Client-facing handoff package.

## Support / Customer Success Agent

Main responsibilities:

- Prepare adoption notes.
- Answer common user questions.
- Capture feedback after delivery.
- Identify follow-up opportunities.

User interaction:

- FAQ/chat after handoff.
- Feedback collection.

Artifacts:

- `support-faq.md`
- `adoption-guide.md`
- `feedback-summary.md`

First version:

- L1 FAQ generation.

Future versions:

- Ticket integration.
- Customer chat.
- Usage analytics summary.

## Knowledge / Memory Agent

Main responsibilities:

- Preserve reusable decisions and lessons.
- Index useful artifacts.
- Prevent repeated mistakes across projects.
- Build the company memory layer.

User interaction:

- User can search previous decisions.
- User can promote a lesson to reusable guidance.

Artifacts:

- `decision-log.md`
- `lessons-learned.md`
- reusable templates and patterns

First version:

- L0 file index and manual decision log.

Future versions:

- Vector search.
- Cross-run memory.
- Similar-project retrieval.

## First Agents To Implement Deeply

The next implementation work should focus on:

1. Intake Agent
2. Product Owner / BA combined scope agent
3. Team Assembler Agent
4. Tech Lead Agent
5. Fullstack Engineer Agent with Codex
6. QA Agent
7. Documentation / Handoff Agent

This is the smallest set that can tell the complete hackathon story.

## Agents To Keep Light For Now

Keep these as docs or optional outputs until the core flow works:

- Sales / Discovery Agent
- Data Engineer Agent
- DevOps / Platform Agent
- Security Review Agent
- Support / Customer Success Agent
- Knowledge / Memory Agent

They are important for the company vision, but not all are needed for the first weekend demo.
