# 03 VM Move And Validation Plan

## VM Baseline

Demo VM baseline:

```text
OS: Ubuntu 24.04 LTS
Size: 2-4 vCPU, 8 GB RAM minimum
Disk: 64 GB minimum
Network: inbound HTTPS for the console if exposed, SSH restricted
Console service: systemd service running Streamlit behind a reverse proxy
App path: user home directory, for example ~/agentic-company
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

## Move And Install

The current demo path can use either a git clone or an exported source archive.
The active VM is updated by extracting a clean `git archive` into the VM user's
project directory:

```bash
~/agentic-company
```

Install Python dependencies from the repository root:

```bash
cd ~/agentic-company
uv sync --extra dev --extra app
```

Never commit real secrets. Keep `.env`, smoke outputs, local Node/Codex installs,
and run outputs ignored and VM-local.

## Environment

The root `.env` used by the platform service must include:

```env
OPENAI_API_KEY=<secret>
CODEX_API_KEY=<secret>
AGENT_CODEX_MODEL=gpt-5.3-codex
AGENTIC_CODEX_REASONING_EFFORT=medium
AGENTIC_CODEX_SERVICE_TIER=fast
AGENTIC_CODEX_SANDBOX=danger-full-access
AGENTIC_CODEX_INHERIT_ENV=true
AGENT_WEB_SEARCH_ENABLED=true
LOG_LEVEL=INFO
AZURE_RESOURCE_GROUP=<demo-resource-group>
AZURE_LOCATION=<azure-region>
AZURE_SUBSCRIPTION_ID=<subscription-id>
```

`OPENAI_API_KEY` is used by LangChain/OpenAI calls. `CODEX_API_KEY` is used by
the npm Codex CLI. Do not rely on legacy aliases.

## Codex CLI Setup

Use the self-contained npm smoke folder instead of a global install or VS Code
extension binary:

```bash
cd ~/agentic-company/ops/codex-npm-smoke
cp .env.example .env
# edit .env and set CODEX_API_KEY
./run-codex-npm-smoke.sh
```

The smoke script:

- downloads official Node.js LTS into `.tools/node/` if needed;
- installs `@openai/codex` locally into `.codex-npm/`;
- runs only the local npm Codex binary;
- requires `CODEX_API_KEY`;
- uses `gpt-5.3-codex`, `medium`, and `service_tier=fast`;
- enables web search with `--search`;
- writes ignored evidence under `outputs/`.

Minimal preflight evidence:

```bash
test -x ops/codex-npm-smoke/.codex-npm/node_modules/.bin/codex
test -x ops/codex-npm-smoke/.tools/node/*/bin/node
```

Expected result:

- Codex command exists.
- API-key auth works without VS Code/device UI.
- Non-interactive `codex exec` returns successfully.

## Azure Setup

Use VM managed identity, not interactive `az login`.

```bash
az login --identity
az account show
az group show -n "$AZURE_RESOURCE_GROUP"
```

Current managed identity access:

```text
Identity: systemAssignedIdentity on the demo VM
Role: Contributor
Scope: the demo resource group only
```

Required Azure evidence commands:

```bash
az account show --query "{name:name, id:id, tenantId:tenantId, user:user.name}" -o json
az acr list -g "$AZURE_RESOURCE_GROUP" -o table
az containerapp env list -g "$AZURE_RESOURCE_GROUP" -o table
```

Do not paste secrets into reports.

## Docker Setup

Install Docker and Compose on Ubuntu:

```bash
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker
```

After adding the user to the `docker` group, reconnect to SSH or run:

```bash
newgrp docker
docker ps
docker compose version
```

The Streamlit service should be restarted after Docker group changes so future
agent subprocesses inherit the updated group:

```bash
sudo systemctl restart agentic-company-console.service
```

## Console Service

The VM serves the console through systemd and Nginx:

```bash
sudo systemctl status agentic-company-console.service --no-pager
sudo systemctl restart agentic-company-console.service
sudo journalctl -u agentic-company-console.service -f
curl -k -I "$AGENTIC_COMPANY_CONSOLE_URL"
```

## Platform Validation

Run local quality checks:

```bash
uv run --extra dev ruff check src tests
uv run --extra dev ruff format --check src tests
uv run --extra dev pytest
```

Run the console through the systemd service and start the demo from the browser.

Capture:

- command used;
- run id;
- start/end timestamps;
- final status;
- deployment URL in VM-local/private evidence, not committed public docs;
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
