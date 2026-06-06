# Agentic Delivery Lab

Product console for running Agentic Company delivery demos in a browser.

## Run The Web App

Install dependencies:

```powershell
uv sync --extra dev --extra app
```

Start the web console:

```powershell
uv run --extra app agentic-web-console
```

Open:

```text
http://127.0.0.1:8503/login
```

Use the web app to register, add your OpenAI key in Settings, create private
projects, follow delivery progress, and open showcase projects.

## Optional Environment

Create a local `.env` if needed:

```env
OPENAI_API_KEY=sk-your-openai-api-key
CODEX_BINARY=path-to-codex
ADMIN_SUPPORT_EMAIL=you@example.com
AGENTIC_WEB_PORT=8503
AGENTIC_CONSOLE_DB_PATH=data/console.db
# Optional Postgres/Redis local compose settings:
# AGENTIC_DATABASE_URL=postgresql://agentic:agentic_dev_password@127.0.0.1:54329/agentic_company
# AGENTIC_REDIS_URL=redis://127.0.0.1:63799/0
PUBLIC_DEMO_RUN_DIR=runs/path-to-showcase-run
PUBLIC_DEMO_PROJECT_NAME=Showcase Project
```

Secrets and generated runs are local only. Do not commit `.env`, `data/`, or
`runs/`.

For a local Postgres/Redis run:

```powershell
docker compose -f docker-compose.dev.yml up -d postgres redis
$env:AGENTIC_DATABASE_URL="postgresql://agentic:agentic_dev_password@127.0.0.1:54329/agentic_company"
$env:AGENTIC_REDIS_URL="redis://127.0.0.1:63799/0"
uv run --extra app agentic-web-console
```

## Checks

```powershell
uv run ruff format --check .
uv run ruff check .
uv run pytest
```
