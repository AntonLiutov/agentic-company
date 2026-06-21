#!/usr/bin/env bash
# One-time, IDEMPOTENT per-host bootstrap for the self-hosted-runner CD. Run as root
# (sudo) on the VM. Re-runnable. Sets up the `deployer` user, the /opt release layout,
# the narrow sudoers grant, and the systemd unit. It does NOT register the GitHub runner
# or write any secret — those are the manual steps printed at the end.
set -euo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
DEPLOY_USER="${DEPLOY_USER:-deployer}"
SERVICE="${SERVICE:-agentic-company-console}"
REPO_URL="${REPO_URL:-https://github.com/AntonLiutov/agentic-company}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo $0)."; exit 1; }

echo ">>> deployer user"
id -u "$DEPLOY_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$DEPLOY_USER"

echo ">>> release layout under $DEPLOY_ROOT"
mkdir -p "$DEPLOY_ROOT"/{releases,shared/data/codex-auth,shared/runs,shared/backups}
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_ROOT"
chmod 0750 "$DEPLOY_ROOT"

echo ">>> shared/.env (prod secrets — never overwritten by a deploy)"
if [ ! -f "$DEPLOY_ROOT/shared/.env" ]; then
  install -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0600 /dev/null "$DEPLOY_ROOT/shared/.env"
  echo "    created empty $DEPLOY_ROOT/shared/.env (0600) — fill from ops/deploy/shared-env.example"
fi

echo ">>> sudoers drop-in (deployer may ONLY restart/status the service)"
install -m 0440 "$SCRIPT_DIR/sudoers-deployer" /etc/sudoers.d/agentic-deployer
visudo -cf /etc/sudoers.d/agentic-deployer

echo ">>> systemd unit $SERVICE"
install -m 0644 "$SCRIPT_DIR/agentic-company-console.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"

echo ">>> uv for $DEPLOY_USER"
sudo -u "$DEPLOY_USER" bash -lc 'command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh'

cat <<EOF

================================================================================
OS-level bootstrap done. MANUAL steps left (once per host):

 1. Fill $DEPLOY_ROOT/shared/.env from ops/deploy/shared-env.example
    (this env's GitHub OAuth app + DNS callback + a FRESH APP_SECRET_KEY + DB url + AGENTIC_WEB_PORT).
    If migrating an existing VM: move the old .env into shared/.env, then DELETE the old loose copy.

 2. Register the GitHub Actions self-hosted runner AS user '$DEPLOY_USER':
      Repo > Settings > Actions > Runners > New self-hosted runner (Linux), then on the VM:
        sudo -u $DEPLOY_USER -i
        mkdir actions-runner && cd actions-runner
        # download per the GitHub page, then:
        ./config.sh --url $REPO_URL --token <REG-TOKEN> --labels self-hosted,vm-dev --unattended
        exit
        sudo ./svc.sh install $DEPLOY_USER && sudo ./svc.sh start    # boot-persistent service
    (Use label vm-staging / vm-prod on the staging / prod hosts instead of vm-dev.)

 3. Repo > Settings > Actions: disable fork-PR runs on self-hosted runners;
    require approval for outside collaborators. (Closes the self-hosted RCE vector.)

 4. Repo > Settings > Environments: create 'dev' (no gate) and 'prod' (REQUIRED REVIEWERS).

 5. First deploy: merge to main -> CI green -> Deploy(dev) flips $DEPLOY_ROOT/current and gates on /healthz.
================================================================================
EOF
