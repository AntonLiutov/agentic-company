#!/usr/bin/env bash
# Release the checked-out commit on THIS VM. Secrets come from Key Vault (rendered into
# shared/.env by the agentic-secrets-fetch oneshot, which we re-run first to pick up any
# rotation); deploy.sh itself NEVER writes shared/.env. The DB is a managed Postgres
# Flexible Server with automated backups + PITR — the primary recovery path for a bad
# schema change. Keep alembic migrations EXPAND-CONTRACT (additive in a release, drops in
# a LATER one) so a code rollback stays compatible with the already-advanced schema.
set -euo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
SERVICE="${SERVICE:-agentic-company-console}"
SHA="${DEPLOY_SHA:-$(git rev-parse HEAD)}"
SRC="${GITHUB_WORKSPACE:-$(pwd)}"
KEEP_RELEASES="${KEEP_RELEASES:-5}"

RELEASES="$DEPLOY_ROOT/releases"; SHARED="$DEPLOY_ROOT/shared"
RELEASE_DIR="$RELEASES/$SHA"; CURRENT="$DEPLOY_ROOT/current"; PREVIOUS="$DEPLOY_ROOT/previous"
log() { printf '\n>>> %s\n' "$*"; }

# Let the worker push over HTTPS using its per-run GH_TOKEN: gh as git's credential helper.
# The token itself is injected per run (build_codex_exec_environment), not stored here.
git config --global credential.https://github.com.helper '!gh auth git-credential' >/dev/null 2>&1 || true

# 0. Re-render secrets from Key Vault (picks up any rotation) before touching anything.
log "refresh secrets from Key Vault"
sudo systemctl restart agentic-secrets-fetch
[ -s "$SHARED/.env" ] || { echo "FATAL: $SHARED/.env not rendered — Key Vault / managed-identity problem; aborting before any change."; exit 1; }

log "Releasing $SHA into $RELEASE_DIR"
mkdir -p "$RELEASES" "$SHARED/data/codex-auth" "$SHARED/runs" "$SHARED/backups"

# 1. Immutable release from the checkout (drop VCS, local caches, and host-state dirs).
rm -rf "$RELEASE_DIR"; mkdir -p "$RELEASE_DIR"
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'runs' --exclude 'data' "$SRC"/ "$RELEASE_DIR"/

# 2. Wire host-local state into the release via symlinks (deploy never writes them).
ln -sfn "$SHARED/.env" "$RELEASE_DIR/.env"
mkdir -p "$RELEASE_DIR/data"
ln -sfn "$SHARED/data/codex-auth" "$RELEASE_DIR/data/codex-auth"
ln -sfn "$SHARED/runs" "$RELEASE_DIR/runs"
printf 'AGENTIC_RELEASE_SHA=%s\n' "$SHA" > "$RELEASE_DIR/.release.env"

# 3. Load env (DB url, web port) WITHOUT printing it; build the release venv.
set -a; . "$SHARED/.env"; set +a
cd "$RELEASE_DIR"
log "uv sync --extra app"; uv sync --extra app

# 4. Best-effort logical backup (managed PITR is the primary safety net), then migrate.
if command -v pg_dump >/dev/null 2>&1 && [ -n "${AGENTIC_DATABASE_URL:-}" ]; then
  ts="$(date +%Y%m%d-%H%M%S)"
  if pg_dump "$AGENTIC_DATABASE_URL" > "$SHARED/backups/pre-$ts-$SHA.sql" 2>"$SHARED/backups/pre-$ts-$SHA.err"; then
    log "pg_dump backup -> $SHARED/backups/pre-$ts-$SHA.sql"
  else
    log "WARN: pg_dump failed (see $SHARED/backups/pre-$ts-$SHA.err; managed PITR still covers recovery)"
  fi
fi
log "agentic-db-upgrade"; uv run --extra app agentic-db-upgrade

# 5. Atomic flip (record previous for rollback), restart.
if [ -L "$CURRENT" ]; then ln -sfn "$(readlink -f "$CURRENT")" "$PREVIOUS.tmp" && mv -Tf "$PREVIOUS.tmp" "$PREVIOUS"; fi
ln -sfn "$RELEASE_DIR" "$CURRENT.tmp" && mv -Tf "$CURRENT.tmp" "$CURRENT"
log "restart $SERVICE"; sudo systemctl restart "$SERVICE"

# 6. Health gate: poll /healthz for THIS sha (90s for a cold uv-synced start), else roll back.
PORT="${AGENTIC_WEB_PORT:-8503}"; ok=""
for _ in $(seq 1 90); do
  body="$(curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
  case "$body" in
    *"\"sha\":\"$SHA\""* | *"\"sha\": \"$SHA\""*) ok=1; break ;;
  esac
  sleep 1
done
if [ -z "$ok" ]; then
  if [ -L "$PREVIOUS" ]; then
    log "HEALTHCHECK FAILED — rolling back to previous"
    ln -sfn "$(readlink -f "$PREVIOUS")" "$CURRENT.tmp" && mv -Tf "$CURRENT.tmp" "$CURRENT"
    sudo systemctl restart "$SERVICE"
  else
    log "HEALTHCHECK FAILED on the FIRST deploy (no previous to roll back to). Service left on $SHA — investigate manually."
  fi
  exit 1
fi
log "DEPLOY OK — $SERVICE healthy on $SHA"

# 7. Prune old releases (keep newest $KEEP_RELEASES; never current/previous).
ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +"$((KEEP_RELEASES + 1))" | while read -r d; do
  case "$(readlink -f "$CURRENT")/" in "$d") continue ;; esac
  case "$(readlink -f "$PREVIOUS" 2>/dev/null)/" in "$d") continue ;; esac
  rm -rf "$d"
done
