#!/bin/bash
# Self-contained host setup for a FRESH Ubuntu VM — runs as root via cloud-init on first
# boot (no repo needed). Installs everything the console + Codex workers need: docker +
# Redis, node + the standalone Codex CLI + Playwright (the same pieces ops/codex-npm-smoke
# and ops/qa-runtime set up locally), uv, az CLI, nginx + certbot. The app code + secrets
# come later (GitHub Actions deploy + bootstrap_vm.sh against Key Vault).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y curl git rsync ca-certificates gnupg nginx certbot python3-certbot-nginx postgresql-client

# --- docker + compose ---
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# --- Redis (Postgres is managed; only Redis runs on the host) ---
docker run -d --restart unless-stopped --name redis -p 127.0.0.1:6379:6379 redis:7-alpine redis-server --appendonly no || true

# --- node 22 + standalone Codex CLI + Playwright (global; AGENTIC_CODEX_BINARY_MODE=auto finds codex on PATH) ---
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs
npm install -g @openai/codex@latest playwright@latest
mkdir -p /opt/agentic-company/shared/pw-browsers
PLAYWRIGHT_BROWSERS_PATH=/opt/agentic-company/shared/pw-browsers npx --yes playwright install --with-deps chromium

# --- uv (Python) system-wide ---
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# --- az CLI (managed-identity Key Vault fetch) ---
command -v az >/dev/null 2>&1 || curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# --- GitHub CLI (workers deliver via gh / the git-pr-workflow skill; agentic-doctor checks it) ---
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y gh
fi

touch /var/log/agentic-host-setup.done
echo "host-setup complete"
