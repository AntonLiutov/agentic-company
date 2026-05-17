# UI Component Specification

## Layout

Use a modern dark SaaS layout:

```text
+---------------------------------------------------------------+
| Top bar: project/run/status/user                              |
+----------+-----------------------------+----------------------+
| Sidebar  | Main content                | Right context panel  |
|          |                             |                      |
| nav      | board/chat/artifacts        | active agent/logs    |
+----------+-----------------------------+----------------------+
```

## Color Tokens

```text
background: #0B1020
surface: #111827
surface_alt: #151E33
card: #182238
border: #2B3652
text: #E5EDF8
muted: #94A3B8
primary: #60A5FA
accent: #A78BFA
success: #22C55E
warning: #F59E0B
danger: #EF4444
```

## Pages

### 1. Login / Register

Fields:
- email
- username
- password

Copy:
- “Create your Agentic Company workspace”
- “Your projects and AI runs stay private to your account.”

Recovery text:
- “Forgot password? Contact the administrator.”

### 2. Dashboard

Cards:
- New Project CTA
- Recent Projects
- Public Demo Project
- System Status

### 3. Project Detail

Tabs:
- Overview
- Board
- Agents
- Artifacts
- Logs
- Handoff

### 4. New Project

Fields:
- project name
- product request
- run mode
- complexity
- voice input button
- format with AI button
- start run

### 5. Board

Columns:
- To Do
- In Progress
- Review
- QA
- Done
- Blocked

Cards:
- task title
- owner agent
- sprint
- short description
- artifact count

### 6. Agents

Show default agent team:
- Head
- BA
- Architect
- PM
- Team Lead
- Fullstack
- QA
- Deployment
- Handoff

Each agent:
- icon/avatar
- model/provider
- purpose
- current status

### 7. Artifacts

Left:
- artifact tree/list

Main:
- preview

Right:
- metadata

### 8. System Check

Checks:
- DB
- OpenAI key
- Codex
- Azure CLI
- Docker
- Internet
- Gemini optional
- Speech input support

## Business Log Labels

Use these names:

- Head Agent → “Coordinator”
- Business Analyst → “Requirements Analyst”
- Architect → “Solution Architect”
- Project Manager → “Delivery Planner”
- Team Lead → “Delivery Lead”
- Fullstack → “Builder”
- QA → “Quality Reviewer”
- Deployment → “Publisher”
- Handoff → “Release Reporter”
- Codex Review → “Quality Review”
- Status Inspector → “Status Check”

## Avoid These In Primary UI

- raw JSON
- raw stack traces
- `codex_exec`
- `events.jsonl`
- `.delivery-state.json`
- file paths as labels
- internal agent execution ids
- raw model prompts
- technical exception text

Show them only in technical details.
