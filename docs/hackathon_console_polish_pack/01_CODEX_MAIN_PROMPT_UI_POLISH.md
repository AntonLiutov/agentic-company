# Codex Task: Hackathon Demo Console Polish, User Isolation, Projects, Board, Artifacts, Providers, and Voice Input

## Mission

Prepare the Agentic Company platform console for a hackathon demo.

The core backend/agentic delivery runtime already exists. Do **not** rewrite the agent runtime. Focus on making the existing platform usable, visually polished, demo-friendly, and separated by user.

This must be done quickly for a demo. Prefer simple working implementation over perfect architecture.

## Existing Platform Context

The repository is `agentic-company`.

The current platform already has:
- Head Agent coordinating specialist agents.
- Business Analyst.
- Architect.
- Project Manager.
- Team Lead.
- Fullstack/Codex.
- QA.
- Deployment.
- Handoff.
- Agent messages.
- Events.
- Run artifacts.
- Streamlit operator console.
- Azure-oriented deployment path.
- Codex CLI execution path.
- Default sample projects and prior generated runs.

The current demo problem:
- Streamlit state can disappear after refresh.
- Different users should not see each other’s projects/runs.
- UI exposes too many technical details.
- Board/run progress is not business-friendly enough.
- Artifacts exist but are not presented cleanly.
- Run logs need to be separated into business-friendly live logs and technical internals.
- Users need a simple place to input API keys and launch runs.
- Optional voice input would improve demo UX.

## High-Level Goal

Build a polished demo console where a user can:

1. Register or login.
2. See only their own projects/runs.
3. Create a new project/run.
4. Enter a product request by text.
5. Optional: dictate the request by voice.
6. Optional: click “Format with AI” to clean up dictated text.
7. Configure their OpenAI API key / provider settings.
8. Start an agentic delivery run.
9. See a beautiful business-friendly board.
10. See agents and workflow status.
11. Open business-readable artifacts.
12. Open the generated app/deployment URL.
13. Open a sample/demo project that is public for all users.
14. Delete their own API key or project.
15. Keep state/history after page refresh.

## Must Not Do

Do not rewrite core orchestration.
Do not change agent prompts unless required for UI display.
Do not expose technical names as the primary UX.
Do not show raw internal paths, raw JSON, raw logs, or Codex internals as the main view.
Do not break existing console run flow.
Do not block the core UI on Gemini or speech-to-text.
Do not implement full enterprise auth, password reset, billing, RBAC, payments, or email sending.

## Tech Direction

Use the current app stack where possible.

If the current console is Streamlit:
- It is okay to keep Streamlit for the hackathon demo.
- Add persistence and user separation around it.
- Polish UI with custom CSS/cards/tabs.
- Do not start a full React rewrite tonight.

If backend DB support is not present:
- Add PostgreSQL support if already easy.
- Otherwise use SQLite for demo with a clear TODO for Postgres.
- Prefer simple persistence over in-memory.

Recommended DB:
- PostgreSQL for Azure VM/demo if feasible.
- SQLite fallback for tonight.

If migrations are already supported:
- Use Alembic.
If not:
- Add a small idempotent initialization script.

## Authentication / User Model

Implement simple demo authentication.

### Required

User can:
- register with username/email/password;
- login;
- logout;
- only see their own projects/runs;
- delete their own stored provider/API key.

### Password recovery

Do not implement real email reset.

Show a simple message:
“If you forgot your password, contact the administrator at `<admin-email>` with your username/email.”

Use environment variable:

```text
ADMIN_SUPPORT_EMAIL
```

Fallback text:
“Contact the platform administrator.”

### Security expectations for demo

- Hash passwords.
- Never print passwords or API keys.
- Do not show API key after saving.
- Allow deleting/replacing API key.
- Do not log secrets in events or live logs.

## User Isolation

Every project/run must belong to a user.

Minimum schema:

```text
users
  id
  email
  username
  password_hash
  created_at

projects
  id
  owner_user_id
  name
  description
  visibility: private | public_demo
  created_at
  updated_at

runs
  id
  project_id
  owner_user_id
  run_dir
  status
  created_at
  updated_at

provider_credentials
  id
  owner_user_id
  provider
  encrypted_or_masked_secret
  created_at
  deleted_at
```

For demo, API keys can be stored encrypted if an app encryption key exists.
If encryption is not feasible tonight:
- store only in session/runtime;
- or store in DB with clear TODO;
- but never show or log the value.
Best option:
- use `APP_SECRET_KEY` / `FERNET_KEY` encryption if easy.

## Default Public Demo Project

Add one public demo project visible to all users.

This should show a completed run from the existing sample.

The public demo project should display:
- project name;
- requirement brief;
- agents involved;
- board status;
- artifacts:
  - BA summary
  - Architecture summary
  - PM roadmap
  - QA report
  - deployment/handoff if available
- generated app URL if available.

Users can view the public demo project but cannot edit/delete it.

## Project History

Authenticated users can see:
- their projects;
- runs under each project;
- run status;
- created time;
- generated app URL if present;
- artifact list;
- final handoff/report if present.

Refresh must not lose project/run history.

## New Project / New Run Flow

User flow:

1. User logs in.
2. User clicks “New Project”.
3. User enters:
   - project name;
   - product request text;
   - optional mode:
     - Simple prototype
     - Internal tool
     - Platform improvement
     - UI/web app
   - optional complexity:
     - simple
     - medium
     - complex
4. User can dictate text through voice input if supported.
5. User can click “Format with AI”.
6. User confirms final request text.
7. User clicks “Start Agentic Run”.
8. Platform creates project/run and calls current agentic flow.

## Voice Input

Implement as optional bonus but useful demo feature.

Preferred fast implementation:
- Browser Web Speech API using `SpeechRecognition` / `webkitSpeechRecognition`.
- Add microphone button near the product request textarea.
- Show “listening…” state.
- Append recognized text into textarea.
- If unavailable, show:
  “Voice input is not supported by this browser. Please type your request.”

Do not block the core demo if voice input is unsupported.

Optional server-side future:
- audio file upload + speech-to-text provider.
- Do not implement tonight unless trivial.

## Format with AI

Add button:

```text
Format with AI
```

Purpose:
- clean dictated text;
- correct grammar;
- add paragraphs;
- preserve meaning;
- do not add new requirements.

If provider key is missing:
- disable button or show “Add API key first”.

The output should replace textarea only after user confirmation, or show preview.

## Provider Settings

Add settings panel.

Minimum:
- OpenAI API key input.
- Codex binary/path status.
- Codex model / reasoning effort display.
- Optional Gemini/Google placeholder.

### OpenAI/Codex

User must understand:
- their OpenAI key is required to run agent decisions / Codex-related work if the platform uses user-provided credentials.
- Codex may cost money.
- complex projects can be expensive.

Business-friendly message:

“Agentic delivery uses AI models and Codex workers. Simple demos may be cheap, but larger products can use significant model credits. Make sure your account has enough balance before running complex projects.”

Do not scare the user. Use a tooltip.

### Suggested tooltip text

“For small demo tasks, usage may be low. For medium/large apps, keep at least $50–$100 available. Very complex apps may require more. Start with a simple task first.”

### Model Settings UI

Show:
- Model provider:
  - OpenAI
  - Google/Gemini optional/stub
- Coordinator model
- Codex model
- Reasoning effort:
  - low
  - medium
  - high
  - xhigh

Business-friendly explanation:
- Low: faster/cheaper, not recommended for complex delivery.
- Medium: default.
- High: better for complex tasks, slower/more expensive.
- XHigh: use only for hard runs.

## Gemini / Google Provider

Do not block tonight on Gemini.

Add the UI placeholder and provider config structure:
- provider: `google_gemini`
- api key field: `GEMINI_API_KEY`
- model field
- status check placeholder

If feasible, add a minimal adapter:
- call Gemini generate content for simple text formatting / summarization only.
- Do not use Gemini for Codex/code execution.

## Free/Sponsor Model Provider

Add provider abstraction placeholder:

```text
Provider:
- OpenAI
- Google/Gemini
- Other/free provider
```

Do not implement unknown sponsor integration unless docs/credentials are available.

## Main UI Design

Make it look premium, modern, simple.

### Visual style

- dark navy/slate background;
- glassy cards;
- blue/violet accent;
- green success badges;
- amber warning;
- red blocked/error;
- rounded panels;
- clear spacing;
- no clutter.

### Navigation

Left sidebar:
- Dashboard
- Projects
- New Project
- Public Demo
- Agents
- Settings
- System Check
- Logout

### Dashboard

Show:
- welcome user;
- CTA “Create New Project”;
- recent projects;
- public demo project;
- system status:
  - OpenAI key: configured / missing
  - Codex: ready / missing
  - Azure: configured / missing
  - DB: connected

### Project Page

Show:
- project title;
- request summary;
- current run status;
- board;
- agent workflow diagram;
- artifacts;
- live business logs;
- final output link.

## Board Design

The board must be business-friendly.

Columns:
- To Do
- In Progress
- Review
- QA
- Done
- Blocked

Cards:
- title;
- owner agent;
- sprint;
- business status;
- short description;
- artifact count;
- click to open details.

Do not use raw internal statuses as primary labels.

Map internal statuses to business labels.

Examples:
- `business_analysis_completed` -> “Business analysis ready”
- `architecture_completed` -> “Architecture ready”
- `project_management_completed` -> “Sprint plan ready”
- `qa_passed` -> “QA passed”
- `deployed` -> “Deployed”
- `handoff_ready` -> “Handoff ready”

## Agent Workflow View

Show agents as cards or an “octopus/team” diagram.

Preferred simple version:
- Head Agent at top.
- Upstream agents:
  - Business Analyst
  - Architect
  - Project Manager
- Delivery coordinator:
  - Team Lead
- Delivery agents:
  - Fullstack
  - QA
  - Deployment
  - Handoff

Use modern cards/icons.

Octopus idea:
- Head Agent can be shown as central coordinator with arms to specialist agents.
- Keep it playful but professional.

Do not spend too much time on complex SVG.
Simple cards and lines are enough.

## Sprint View

Show collapsible sprints.

Each sprint:
- sprint title;
- goal;
- progress percentage;
- collapsible task list.

Task cards:
- owner;
- status;
- short business description;
- artifacts button;
- logs button.

## Artifact Viewer

Artifacts should be easy to inspect.

Business-facing default:
- Markdown summary
- HTML report
- Mermaid diagram rendered if possible
- roadmap CSV as table
- screenshots/images

Technical artifacts should be behind:
- “Show technical details”
- collapsed by default.

Support:
- Markdown render
- JSON render/table
- CSV table
- Mermaid diagram render if easy
- HTML report preview or link
- image preview

For Mermaid:
- If full rendering is hard tonight, show:
  - formatted Mermaid text;
  - button to copy;
  - clear label “Architecture diagram source”.
- Better: render Mermaid using existing JS library if easy.

## Live Logs

Show business-friendly live activity.

Examples:
- “Business Analyst is preparing requirements.”
- “Architect is designing the solution.”
- “Project Manager is creating sprint plan.”
- “Team Lead started Sprint 1.”
- “Fullstack Agent is building feature F1.”
- “QA Agent is validating feature F1.”
- “Deployment Agent is publishing the app.”
- “Handoff Agent is preparing report.”

Raw Codex logs:
- hide behind “Technical logs”.
- never show as primary UI.
- never expose secrets.

If Codex Review or Status Inspector runs:
- show friendly name:
  - “Quality Review”
  - “Status Check”
  - “Evidence Inspector”

## Agents Settings / Catalog

Show selected/default agents.

Default team:
- Head Agent
- Business Analyst
- Architect
- Project Manager
- Team Lead
- Fullstack
- QA
- Deployment
- Handoff

Each agent card:
- name;
- purpose;
- model/provider;
- status;
- brief description.

Allow model selection if easy:
- coordinator model
- worker/Codex model
- reasoning effort

Do not implement full custom agent builder tonight.
Add placeholder:
“Custom agents coming soon.”

## System Check

Create a clear System Check page.

Checks:
- Database connection
- OpenAI key configured
- Codex binary available
- Azure CLI configured
- Docker available
- Internet access
- Gemini key optional
- Speech input support: browser-only check

Do not block platform if optional checks fail.

## Database / Migration

Add DB persistence if not already present.

Minimum:
- users
- projects
- runs
- artifacts metadata
- provider credentials/settings
- event metadata optional

Use Alembic if possible.

On app startup:
- run migrations automatically if safe;
- or provide one command clearly.

If Alembic is too much tonight:
- use SQLAlchemy create_all for demo and add TODO.

## WebSocket / Live Updates

Preferred:
- WebSocket for live logs and board updates.

Fallback:
- polling every 2–5 seconds.

For tonight, polling is acceptable if WebSocket is risky.

If WebSocket implemented:
- use project/run-specific channels;
- enforce user ownership;
- no cross-user leakage.

## User Data Safety

Ensure:
- user can see only own private projects;
- public demo project is read-only;
- API keys are not visible after save;
- deleting key removes it or marks deleted;
- logout clears session.

## Integration With Existing Runs

The app should be able to load existing run artifacts from run folders.

If a run directory exists:
- read `.delivery-state.json`;
- read `events.jsonl`;
- discover artifacts;
- map board/status;
- show handoff/public URL if available.

## Demo Acceptance Criteria

This is done when:

1. User can register/login.
2. User sees only their projects.
3. User can create project/run.
4. User can save/delete OpenAI API key.
5. User can type request.
6. Optional: user can dictate request through browser speech if supported.
7. Optional: user can format request with AI.
8. User can start run.
9. Board shows agent/stage/task progress.
10. Live business logs appear.
11. Artifacts are visible.
12. Public demo project is visible to all users.
13. Technical logs are hidden by default.
14. UI looks modern and premium.
15. Refresh does not lose projects/history.
16. Existing backend/agentic flow still works.
17. No secrets are displayed in UI/logs.

## Priority Order For Tonight

### Priority 1 — Must Have
- Login/register.
- User isolation.
- Projects/runs persistence.
- Dashboard/projects pages.
- Existing run/artifact viewer.
- Board visualization.
- Settings for OpenAI key.
- Hide technical details by default.
- Public demo project.

### Priority 2 — Should Have
- New Project form.
- Start run button.
- Business live logs.
- System Check page.
- Mermaid/artifact rendering.
- Better agent cards.

### Priority 3 — Nice To Have
- Voice input through Web Speech API.
- Format with AI.
- Gemini provider placeholder/minimal adapter.
- WebSocket live updates.

### Priority 4 — Defer If Time Runs Out
- Full custom agents.
- Full workflow builder.
- Real password recovery.
- Full Gemini routing.
- Advanced voice transcription provider.
- React rewrite.

## Instruction To Codex

Implement this as a hackathon demo polish pass.

Do not overbuild.
Do not break existing agent runtime.
Do not expose raw technical internals as the primary UI.
Make the console look like a real product demo.
Keep changes focused and testable.

If the task becomes too large:
1. First implement login/user isolation/projects/artifacts/board.
2. Then implement settings/API key.
3. Then implement voice/Gemini as optional stretch.
