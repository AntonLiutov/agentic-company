# 09 Agent Runtime Maturity Model

This document defines the versions an agent can have over time. The goal is to avoid a false choice between "hardcoded workflow" and "complex autonomous agent." Most useful agents should start simple and earn complexity only when the product needs it.

## Why This Matters

The product is an agentic delivery system, but not every role needs the same runtime. Some roles can be deterministic for a long time. Some need simple LLM calls. Some need coding agents, Figma tools, browser tools, or graph orchestration.

The architecture should support several agent implementation levels behind the same role contract.

## Maturity Levels

| Level | Name | Description | Best For | Risks |
| --- | --- | --- | --- | --- |
| L0 | Hardcoded | Deterministic rules, templates, and static mappings | Early pipeline, predictable decisions, demos | Rigid, limited adaptation |
| L1 | Simple LLM Call | One prompt in, one structured response out | Summaries, scope drafts, clarifying questions | Inconsistent output without schemas |
| L2 | Simple LLM Agent | LLM with role prompt, memory of current run artifacts, and structured output | Product, BA, architecture, QA writing | May still hallucinate or skip constraints |
| L3 | Tool Executor | LLM can call selected tools through a controlled executor | Search, docs lookup, issue creation, artifact validation | Tool misuse, more error states |
| L4 | LangChain Executor | Uses LangChain-style chains/tools for integrations and repeatable calls | Retrieval, document workflows, model/tool wrappers | Framework complexity |
| L5 | LangGraph Workflow Agent | Graph-based state machine with loops, branches, approvals, retries | Multi-step workflows, human checkpoints, recovery | Too heavy if used too early |
| L6 | Codex Agent | Coding agent that can read, edit, and test real project files | Engineering, QA code review, documentation updates | Needs strict workspace boundaries |
| L7 | Specialized External Agent | Uses domain-specific tools such as Claude + Figma, browser automation, deployment APIs | UX design, visual review, DevOps, multimodal tasks | Provider and tool coupling |
| L8 | Multi-Agent Team | Multiple agents run with coordination, arbitration, and shared artifacts | Mature delivery automation | Hard to debug without strong observability |

## Default Strategy

Start most agents at L0 or L1.

Move an agent up a level only when:

- Its current output is blocking product quality.
- It needs external tools.
- It needs to inspect or modify files.
- It needs to branch, retry, or ask for human approval.
- The role is important in the demo story.

## Recommended Weekend Levels

| Agent | Weekend Level | Reason |
| --- | --- | --- |
| Intake Agent | L0 -> L1 | Current parser works; LLM can improve messy inputs later |
| Team Assembler Agent | L0 | Rules are enough for one workflow |
| Product Owner Agent | L1 | Good first use of a simple structured LLM call |
| Business Analyst Agent | L1 | Can expand requirements and edge cases |
| Architecture Agent | L1 | Can produce architecture notes from scope |
| PM / Delivery Manager Agent | L0 -> L1 | Deterministic sprint plan first, LLM improvements later |
| Design Agent | L1 now, L7 later | Text design brief now; Claude + Figma later |
| Fullstack Engineer Agent | L6 | This is the first real execution proof |
| QA Agent | L3 now, L6 later | Executes local, Docker, and Playwright checks now; Codex repair/review later |
| Documentation / Handoff Agent | L1 | Strong artifact synthesis use case |
| Demo / Pitch Agent | L1 | Great for slides, script, positioning |

## Runtime Contract

Every agent implementation, regardless of maturity level, should share the same basic contract:

```text
AgentInput
  run_id
  agent_id
  agent_version
  maturity_level
  input_artifacts
  instructions
  constraints
  expected_outputs

AgentOutput
  status
  output_artifacts
  decisions
  open_questions
  events
```

This lets us replace a hardcoded agent with an LLM-backed agent without changing the whole pipeline.

## Interaction Modes

Agents may interact through several channels:

| Interaction Mode | Description | Best Agents |
| --- | --- | --- |
| Form input | Structured fields in the web UI | Intake, Product Owner |
| Document upload | Requirements, specs, PDFs, notes | Intake, BA, Architecture |
| Chat refinement | User answers clarifying questions | Intake, Product Owner, BA |
| Voice intake | User talks through an idea, transcript becomes input | Intake, Sales / Discovery, PM |
| Artifact review | User approves or edits generated artifacts | PM, Architecture, QA |
| Tool execution | Agent calls code, Figma, deployment, or browser tools | Engineer, Design, DevOps |
| Human approval gate | User confirms before expensive or risky action | Execution, deployment, external APIs |

Voice should be treated as an input mode, not a separate agent. The voice layer can create a transcript that feeds the Intake Agent.

## Provider Strategy

The provider should be an implementation detail, not the identity of the agent.

Examples:

- Intake Agent can start as hardcoded parsing, then simple LLM, then voice + LLM.
- Design Agent can start as a written UX brief, then Claude visual reasoning, then Figma integration.
- Fullstack Engineer Agent can start with Codex because file editing is core to the role.
- QA Agent now acts as a tool executor for local, Docker, and Playwright/browser checks; it should
  grow next into Codex-assisted repair and review.

## LangChain And LangGraph Guidance

Use no framework while the workflow is linear and inspectable.

Use a simple custom runner first when:

- Agent input and output are files.
- There are no complex loops.
- The UI only needs a timeline of steps.
- We are still learning the right artifacts.

Consider LangChain when:

- Tool calls and retrieval integrations become repetitive.
- We need common model/tool wrappers.
- We need document loaders, retrievers, or structured output helpers at scale.

Consider LangGraph when:

- Workflows branch based on state.
- Agents retry after QA failure.
- Human approval nodes become common.
- Multiple agents run in parallel.
- We need durable graph state and recovery.

## Implementation Sequence

1. Keep current deterministic pipeline.
2. Add `agent_version` and `maturity_level` to event metadata.
3. Add simple `AgentRunner` protocol.
4. Add `DeterministicRunner`.
5. Add `LLMRunner` for Product Owner, BA, Architecture, Documentation.
6. Add `CodexRunner` for Fullstack Engineer. Done for the first vertical slice.
7. Add tool-executing QA and deployment runners. Done for the first vertical slice.
8. Add Codex repair mode for failed QA.
9. Add `DesignRunner` placeholder for Claude + Figma.
10. Add `GraphRunner` only after loops and approvals are truly needed.

## Product Message

The product should not promise that every role is a heavy autonomous agent on day one. The better message is:

> Each company role has a clear contract. Some roles are deterministic, some are LLM-powered, and some become specialized tool agents when execution requires it.

That is mature, honest, and more credible for judges and investors.
