#!/usr/bin/env bash
# One-time, IDEMPOTENT per-host bootstrap for the self-hosted-runner CD with Key Vault
# secret rendering. Run as root (sudo) on the VM AFTER infra/provision.sh has created
# this env's Key Vault + Postgres and seeded the secrets, and AFTER ops/deploy/env.nonsecret.<env>
# has the right AGENTIC_KEY_VAULT + AGENTIC_PG_HOST. Re-runnable. Installs NO secret by
# hand — shared/.env is machine-rendered from Key Vault via the VM's managed identity.
set -euo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
DEPLOY_USER="${DEPLOY_USER:-deployer}"
SERVICE="${SERVICE:-agentic-company-console}"
ENV_NAME="${ENV_NAME:-dev}"
REPO_URL="${REPO_URL:-https://github.com/AntonLiutov/agentic-company}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NONSECRET_SRC="$SCRIPT_DIR/env.nonsecret.$ENV_NAME"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo $0)."; exit 1; }
[ -f "$NONSECRET_SRC" ] || { echo "FATAL: missing $NONSECRET_SRC (set ENV_NAME or create it)."; exit 1; }

echo ">>> packages: az CLI + postgresql-client + rsync"
command -v az >/dev/null 2>&1 || curl -sL https://aka.ms/InstallAzureCLIDeb | bash
command -v psql >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y postgresql-client; }
command -v rsync >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y rsync; }
# gh: the console uses the GitHub CLI for the board mirror + repo ensure. bootstrap ensures
# it even if an older cloud-init host-setup predated the gh step (idempotent).
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq && apt-get install -y gh
fi

echo ">>> deployer user"
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"

echo ">>> release layout under $DEPLOY_ROOT"
mkdir -p "$DEPLOY_ROOT"/{releases,shared/bin,shared/data/codex-auth,shared/runs,shared/backups}
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_ROOT"
chmod 0750 "$DEPLOY_ROOT"

echo ">>> secret render: fetch-secrets.sh + non-secret config (shared/.env is rendered from Key Vault, never hand-filled)"
install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 "$SCRIPT_DIR/bin/fetch-secrets.sh" "$DEPLOY_ROOT/shared/bin/fetch-secrets.sh"
install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0640 "$NONSECRET_SRC" "$DEPLOY_ROOT/shared/env.nonsecret"

echo ">>> systemd units (secrets oneshot + console) + sudoers"
install -m 0644 "$SCRIPT_DIR/agentic-secrets-fetch.service" /etc/systemd/system/agentic-secrets-fetch.service
install -m 0644 "$SCRIPT_DIR/agentic-company-console.service" "/etc/systemd/system/$SERVICE.service"
install -m 0440 "$SCRIPT_DIR/sudoers-deployer" /etc/sudoers.d/agentic-deployer
visudo -cf /etc/sudoers.d/agentic-deployer
systemctl daemon-reload
systemctl enable agentic-secrets-fetch.service "$SERVICE"

echo ">>> uv for $DEPLOY_USER"
sudo -u "$DEPLOY_USER" bash -lc 'command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh'

# The console unit's ExecStart is /usr/local/bin/uv — make sure that path resolves whether
# uv came from host-setup (system-wide) or the user-local install above.
if [ ! -x /usr/local/bin/uv ]; then
  UVPATH="$(command -v uv 2>/dev/null || true)"
  [ -z "$UVPATH" ] && [ -x "/home/$DEPLOY_USER/.local/bin/uv" ] && UVPATH="/home/$DEPLOY_USER/.local/bin/uv"
  if [ -n "$UVPATH" ]; then ln -sf "$UVPATH" /usr/local/bin/uv; else echo "WARN: uv not found — the console service needs /usr/local/bin/uv"; fi
fi

echo ">>> render secrets now (managed identity -> Key Vault) and verify"
systemctl start agentic-secrets-fetch.service
test -s "$DEPLOY_ROOT/shared/.env" \
  && echo "    OK: shared/.env rendered from Key Vault" \
  || { echo "FATAL: shared/.env not rendered. Check: the VM has a system-assigned managed identity with 'Key Vault Secrets User' on the vault, NSG egress to *.vault.azure.net is open, and env.nonsecret has the right AGENTIC_KEY_VAULT."; exit 1; }

cat <<EOF

================================================================================
OS-level bootstrap done; secrets render from Key Vault. MANUAL steps left (once):

 1. Register the GitHub Actions self-hosted runner AS user '$DEPLOY_USER':
      Repo > Settings > Actions > Runners > New self-hosted runner (Linux), then:
        sudo -u $DEPLOY_USER -i
        mkdir actions-runner && cd actions-runner   # download per the GitHub page
        ./config.sh --url $REPO_URL --token <REG-TOKEN> --labels self-hosted,vm-$ENV_NAME --unattended
        exit
        sudo ./svc.sh install $DEPLOY_USER && sudo ./svc.sh start

 2. Repo > Settings > Actions: disable fork-PR runs on self-hosted runners;
    require approval for outside collaborators.

 3. Repo > Settings > Environments: 'dev' (no gate) and 'prod' (REQUIRED REVIEWERS).

 4. First deploy: the merge that INTRODUCES deploy.yml to main does NOT auto-deploy
    (workflow_run only fires from the default branch). Trigger the first one manually
    (re-run CI on main, or push a trivial commit) — thereafter merges auto-deploy.
================================================================================
EOF
