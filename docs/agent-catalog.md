# Agent Catalog

This document defines the starting company roster for `agentic-company`.

## Core Orchestration Agents

| Agent | Primary Purpose | Typical Activation |
| --- | --- | --- |
| Intake Agent | Normalize raw requests into a usable brief | Every engagement |
| Team Assembler Agent | Select the smallest effective team for the work | Every engagement |
| Knowledge / Memory Agent | Preserve reusable knowledge, decisions, and context | Multi-step or long-running work |

## Business And Delivery Agents

| Agent | Primary Purpose | Typical Activation |
| --- | --- | --- |
| Sales / Discovery Agent | Clarify opportunity, business context, and proposal assumptions | New client work |
| Product Manager Agent | Define scope, priorities, and success criteria | Most product work |
| Business Analyst Agent | Refine detailed requirements and edge cases | Medium or high ambiguity |
| Project / Delivery Manager Agent | Coordinate sequencing, risks, and timelines | Multi-role delivery |
| Documentation / Handoff Agent | Package the final delivery for client use and transition | End of delivery |
| Support / Customer Success Agent | Handle adoption, support, and post-handoff follow-up | After handoff |

## Design And Architecture Agents

| Agent | Primary Purpose | Typical Activation |
| --- | --- | --- |
| UX / Product Designer Agent | Shape user flows, interaction design, and usability | User-facing products |
| Solution Architect Agent | Define the target architecture and technical tradeoffs | Most non-trivial builds |
| Security Review Agent | Identify material security, privacy, and access risks | Data-sensitive or external-facing work |

## Implementation Agents

| Agent | Primary Purpose | Typical Activation |
| --- | --- | --- |
| Tech Lead Agent | Translate architecture into executable technical delivery | Multi-engineer or risky implementation |
| Frontend Engineer Agent | Build user interfaces and client-side behavior | Web or front-office apps |
| Backend Engineer Agent | Build APIs, services, persistence, and business logic | Most applications |
| Fullstack Agent | Execute across frontend and backend for lean delivery | Small PoCs and compact teams |
| AI / LLM Engineer Agent | Design prompting, retrieval, model usage, and evaluation | AI-enabled products |
| Data Engineer Agent | Build pipelines, storage flows, and data quality controls | Data-heavy solutions |
| DevOps / Platform Agent | Prepare environments, deployment, and operational stability | Deployable systems |
| QA Agent | Validate behavior, quality, and delivery readiness | Every build worth handing off |

## Usage Guidance

- The roster represents company capabilities, not the default active team
- Most projects should activate only a subset of these agents
- The Team Assembler Agent is responsible for selecting and merging roles where appropriate
