# Agentic Delivery Lab

Product console for running Agentic Company delivery demos in a browser.

## Run The Web App

Install dependencies:

```powershell
uv sync --extra dev --extra app
```

Start local infrastructure:

```powershell
docker compose -f docker-compose.dev.yml up -d postgres redis
```

Run migrations and start the FastAPI console on the host:

```powershell
uv run --extra app agentic-db-upgrade
uv run --extra app agentic-web-console
```

Open:

```text
http://127.0.0.1:8503/login
```

Use the web app to register, add your OpenAI key in Settings, create private
projects, follow delivery progress, and open showcase projects.

## Optional Environment

Create a local `.env` with infrastructure defaults and optional provider keys:

```env
AGENTIC_DATABASE_URL=postgresql://agentic:agentic_dev_password@127.0.0.1:54329/agentic_company
AGENTIC_POSTGRES_POOL_MIN=1
AGENTIC_POSTGRES_POOL_MAX=10
AGENTIC_REDIS_URL=redis://127.0.0.1:63799/0
AGENTIC_WEB_PORT=8503

# Optional local defaults:
CODEX_BINARY=path-to-codex
ADMIN_SUPPORT_EMAIL=you@example.com
PUBLIC_DEMO_RUN_DIR=runs/path-to-showcase-run
PUBLIC_DEMO_PROJECT_NAME=Showcase Project
```

Provider keys can be added in the web Settings screen. Secrets and generated
runs are local only. Do not commit `.env` or `runs/`.

## Checks

```powershell
uv run ruff format --check .
uv run ruff check .
uv run --extra app pytest
```
