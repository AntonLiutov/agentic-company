# 03 VM Move And Validation Plan

## VM Baseline

Recommended minimum for the demo VM:

```text
OS: Ubuntu 22.04 or 24.04 LTS
CPU: 2-4 vCPU
RAM: 8 GB minimum, 16 GB preferred
Disk: 64 GB minimum
Network: inbound HTTP/HTTPS for console if exposed, SSH restricted
```

Required tools:

```text
git
curl
node + npm
python 3.11+
uv
docker
docker compose
azure-cli
codex CLI
```

## Clone And Install

```bash
git clone <repo-url>
cd agentic-company
uv sync --extra dev
```

If the repo uses environment-specific files, create them from examples:

```bash
cp .env.example .env
```

Never commit real secrets.

## Codex CLI Setup

Install Codex with npm:

```bash
npm install -g @openai/codex
codex --version
```

Authenticate on the VM with API-key login:

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

Preflight checks:

```bash
codex --version
codex exec "Say ready and print the current working directory."
```

Expected result:

- Codex command exists.
- API-key auth works without VS Code/device UI.
- Non-interactive `codex exec` returns successfully.

## Azure Setup

Use Azure CLI login appropriate for the VM:

```bash
az login
az account show
```

For unattended or longer-running demo use, prefer a service principal or managed identity if the VM
setup supports it.

Required Azure evidence:

```bash
az account show --query "{name:name, id:id, tenantId:tenantId}" -o json
```

Do not paste secrets into reports.

## Platform Validation

Run local quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Run the console or platform entry point according to the current README.

Capture:

- command used;
- run id;
- start/end timestamps;
- final status;
- deployment URL;
- QA report refs;
- handoff refs;
- screenshots.

## VM Validation Report Template

```markdown
# VM Validation Report

Run id:
VM:
Branch:
Commit:
Date:

## Checks

- Ruff:
- Format:
- Pytest:
- Codex preflight:
- Azure account:
- Docker:

## End-to-End Run

- Status:
- Features:
- QA:
- Deployment:
- Public URL:
- Handoff refs:

## Evidence

- Logs:
- Screenshots:
- Reports:

## Remaining Risks

- ...
```

