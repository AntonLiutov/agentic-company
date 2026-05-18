# Hackathon UI Mockup Requirements

## Global Style

Use a modern premium dark SaaS style.

### Colors

- Background: `#080D19` or `#0B1020`
- Panel: `#111827`
- Secondary panel: `#151E33`
- Card: `#182238`
- Border: `#2B3652`
- Text: `#E5EDF8`
- Muted text: `#94A3B8`
- Primary blue: `#60A5FA`
- Accent violet: `#A78BFA`
- Success green: `#22C55E`
- Warning amber: `#F59E0B`
- Danger red: `#EF4444`

### Typography

Use clean sans-serif fonts:
- Inter / system UI / Segoe UI / Arial
- Page title: 30–36px bold
- Card title: 22–24px bold
- Body: 16–18px
- Small labels: 12–14px

### UI Rules

- Left sidebar always visible on desktop.
- Main content uses cards with rounded corners.
- Status badges should be colorful and readable.
- Technical logs/details are hidden by default.
- Business-friendly language is primary.
- No raw `events.jsonl`, `.delivery-state.json`, `codex_exec`, file paths, stack traces, or secret values in primary UI.

## Screen 01 — Login / Register

Purpose:
- User creates or enters private workspace.
- User data and projects are isolated.

Requirements:
- Fields: email, username, password.
- Register/login can be one demo form.
- Passwords must be hashed.
- No real password reset tonight.
- Show admin contact message for forgotten password.

## Screen 02 — Dashboard

Purpose:
- Show recent private projects, public demo, and system health.

Requirements:
- CTA for New Project.
- Recent projects table.
- Public demo card.
- System badges: OpenAI, Codex, Azure, DB.
- Must filter private projects by current user.

## Screen 03 — New Project with Voice and AI Format

Purpose:
- User creates a new agentic delivery run.

Requirements:
- Fields: project name, product request, mode, complexity, provider, reasoning.
- Voice input button using browser Web Speech API if available.
- Fallback if voice unsupported.
- Format with AI button cleans grammar without adding requirements.
- Cost tooltip explaining complex runs may require model balance.

## Screen 04 — Project Workspace Chat

Purpose:
- User talks to Head/Coordinator and sees run context.

Requirements:
- Central chat is Head Agent/user interaction.
- Right panel shows current stage, current task, artifacts, business logs.
- Specialist logs are not raw by default.

## Screen 05 — Delivery Board and Sprints

Purpose:
- Show real delivery progress as a business-friendly board.

Columns:
- To Do
- In Progress
- Review
- QA
- Done
- Blocked

Requirements:
- Cards show title, owner agent, sprint, artifact count.
- Collapsible sprint drawer.
- Internal statuses mapped to business labels.

## Screen 06 — Agent Workflow / Octopus

Purpose:
- Explain how agents are connected.

Requirements:
- Head/Coordinator central.
- Upstream agents: BA, Architect, PM.
- Delivery agents: Team Lead, Builder, QA, Publisher, Reporter.
- Simple cards/lines are enough.

## Screen 07 — Artifact Viewer

Purpose:
- Open business artifacts easily.

Support:
- Markdown
- JSON
- CSV
- Mermaid
- HTML report
- images/screenshots

Requirements:
- Business reports first.
- Technical details collapsed.
- Mermaid render if possible; otherwise show formatted source and copy button.

## Screen 08 — Settings and Providers

Purpose:
- Configure provider keys and system readiness.

Requirements:
- OpenAI API key.
- Codex binary/status.
- Coordinator model.
- Codex model.
- Reasoning effort.
- Gemini optional placeholder.
- System checks: DB, Internet, Docker, Azure CLI, Codex, speech support.

Secrets:
- Never show full key after save.
- Allow deleting provider key.

## Screen 09 — Public Demo Project

Purpose:
- Read-only sample project visible to all users.

Requirements:
- Open generated app.
- Open handoff report.
- Show metrics: time, cost, QA, deployment.
- Show agent journey and artifacts.

## Screen 10 — Task Detail / Business Logs

Purpose:
- Click board task to inspect details.

Requirements:
- Business description.
- Acceptance criteria.
- Owner agent.
- Status.
- Business logs.
- Artifacts.
- Technical logs collapsed.

## Implementation Priority

Must have:
1. Login/register
2. User isolation
3. Projects/history
4. Public demo
5. Board
6. Artifact viewer
7. Settings/provider key
8. Modern CSS

Should have:
1. New project/run form
2. Business logs
3. System check
4. Agent workflow view

Nice to have:
1. Voice input
2. Format with AI
3. Gemini provider placeholder
4. WebSocket; polling is acceptable if faster
