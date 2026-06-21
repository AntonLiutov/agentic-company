#!/usr/bin/env bash
# Release the checked-out commit on THIS VM: immutable release dir + atomic symlink
# flip + migrate + restart + /healthz gate + auto-rollback. Invoked by the self-hosted
# GitHub Actions runner (user `deployer`) AFTER actions/checkout, from the checkout tree.
#
# Hard rules baked in structurally:
#   - the prod .env lives ONLY in $DEPLOY_ROOT/shared/.env and is symlinked into the
#     release; this script NEVER writes/overwrites it.
#   - no secrets are echoed; secrets never flow through `az vm run-command`.
set -euo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
SERVICE="${SERVICE:-agentic-company-console}"
SHA="${DEPLOY_SHA:-$(git rev-parse HEAD)}"
SRC="${GITHUB_WORKSPACE:-$(pwd)}"          # the actions/checkout tree
KEEP_RELEASES="${KEEP_RELEASES:-5}"

RELEASES="$DEPLOY_ROOT/releases"
SHARED="$DEPLOY_ROOT/shared"
RELEASE_DIR="$RELEASES/$SHA"
CURRENT="$DEPLOY_ROOT/current"
PREVIOUS="$DEPLOY_ROOT/previous"

log() { printf '\n>>> %s\n' "$*"; }

[ -f "$SHARED/.env" ] || { echo "FATAL: $SHARED/.env missing — run bootstrap_vm.sh and fill it first."; exit 1; }

log "Releasing $SHA into $RELEASE_DIR"
mkdir -p "$RELEASES" "$SHARED/data/codex-auth" "$SHARED/runs" "$SHARED/backups"

# 1. Materialize an immutable release from the checkout (drop VCS + local caches).
rm -rf "$RELEASE_DIR"; mkdir -p "$RELEASE_DIR"
rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'runs' "$SRC"/ "$RELEASE_DIR"/

# 2. Wire host-local mutable state into the release via symlinks (deploy never writes them).
ln -sfn "$SHARED/.env" "$RELEASE_DIR/.env"
mkdir -p "$RELEASE_DIR/data"
ln -sfn "$SHARED/data/codex-auth" "$RELEASE_DIR/data/codex-auth"
ln -sfn "$SHARED/runs" "$RELEASE_DIR/runs"
printf 'AGENTIC_RELEASE_SHA=%s\n' "$SHA" > "$RELEASE_DIR/.release.env"

# 3. Load prod env (DB url etc.) WITHOUT printing it; build the release venv.
set -a; . "$SHARED/.env"; set +a
cd "$RELEASE_DIR"
log "uv sync --extra app"
uv sync --extra app

# 4. Best-effort DB backup before a possibly-irreversible migration, then migrate.
if command -v pg_dump >/dev/null 2>&1 && [ -n "${AGENTIC_DATABASE_URL:-}" ]; then
  ts="$(date +%Y%m%d-%H%M%S)"
  log "pg_dump backup -> $SHARED/backups/pre-$ts-$SHA.sql"
  pg_dump "$AGENTIC_DATABASE_URL" > "$SHARED/backups/pre-$ts-$SHA.sql" || log "WARN: pg_dump failed (continuing)"
fi
log "agentic-db-upgrade (alembic head)"
uv run --extra app agentic-db-upgrade

# 5. Atomic flip: record the live release as `previous`, point `current` at this one.
if [ -L "$CURRENT" ]; then
  ln -sfn "$(readlink -f "$CURRENT")" "$PREVIOUS.tmp" && mv -Tf "$PREVIOUS.tmp" "$PREVIOUS"
fi
ln -sfn "$RELEASE_DIR" "$CURRENT.tmp" && mv -Tf "$CURRENT.tmp" "$CURRENT"

log "restart $SERVICE"
sudo systemctl restart "$SERVICE"

# 6. Health gate: poll /healthz until it reports THIS sha; else roll back to previous.
PORT="${AGENTIC_WEB_PORT:-8503}"
ok=""
for _ in $(seq 1 30); do
  body="$(curl -fsS "http://127.0.0.1:$PORT/healthz" 2>/dev/null || true)"
  case "$body" in *"$SHA"*) ok=1; break ;; esac
  sleep 1
done
if [ -z "$ok" ]; then
  log "HEALTHCHECK FAILED — rolling back"
  if [ -L "$PREVIOUS" ]; then
    ln -sfn "$(readlink -f "$PREVIOUS")" "$CURRENT.tmp" && mv -Tf "$CURRENT.tmp" "$CURRENT"
    sudo systemctl restart "$SERVICE"
  fi
  exit 1
fi
log "DEPLOY OK — $SERVICE healthy on $SHA"

# 7. Prune old releases (keep the newest $KEEP_RELEASES + whatever current/previous point at).
ls -1dt "$RELEASES"/*/ 2>/dev/null | tail -n +"$((KEEP_RELEASES + 1))" | while read -r d; do
  case "$(readlink -f "$CURRENT")/" in "$d") continue ;; esac
  case "$(readlink -f "$PREVIOUS" 2>/dev/null)/" in "$d") continue ;; esac
  rm -rf "$d"
done
