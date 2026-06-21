#!/usr/bin/env bash
# Render /opt/agentic-company/shared/.env from Azure Key Vault using the VM's
# system-assigned MANAGED IDENTITY (no secret needed to fetch secrets). Runs as a
# systemd oneshot BEFORE the console service, and again on each deploy so a rotated
# secret is picked up. Secrets are NEVER echoed. On any failure the LAST-GOOD .env is
# kept (we render to a temp file and mv only on success).
set -euo pipefail
umask 077
tmp=""
trap '[ -n "${tmp:-}" ] && rm -f "$tmp" 2>/dev/null || true' EXIT

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
SHARED="$DEPLOY_ROOT/shared"
ENV_OUT="$SHARED/.env"
NONSECRET="${NONSECRET_FILE:-$SHARED/env.nonsecret}"

[ -f "$NONSECRET" ] || { echo "FATAL: missing non-secret config $NONSECRET"; exit 1; }
# shellcheck disable=SC1090
. "$NONSECRET"   # AGENTIC_KEY_VAULT, AGENTIC_PG_HOST/DB/USER, AGENTIC_WEB_*, callback, redis, codex, profile
: "${AGENTIC_KEY_VAULT:?set AGENTIC_KEY_VAULT in $NONSECRET}"
: "${AGENTIC_PG_HOST:?set AGENTIC_PG_HOST in $NONSECRET}"
case "$AGENTIC_KEY_VAULT$AGENTIC_PG_HOST${GITHUB_OAUTH_CALLBACK_URL:-}" in
  *__SET_*) echo "FATAL: $NONSECRET still has placeholder values — set the real vault/PG host/callback from provision.sh output."; exit 1 ;;
esac

# Managed-identity login, with a short retry while IMDS warms up at boot.
for i in 1 2 3 4 5; do
  if az login --identity --only-show-errors >/dev/null 2>&1; then break; fi
  [ "$i" = 5 ] && { echo "FATAL: az login --identity failed (managed identity / IMDS not ready)"; exit 1; }
  sleep 3
done

# Read a secret, retrying through RBAC role-assignment propagation lag (~5 min) — the
# 'Key Vault Secrets User' grant can take minutes to reach the data plane after provision.
kv() {
  local name="$1" val="" i
  for i in $(seq 1 30); do
    val="$(az keyvault secret show --vault-name "$AGENTIC_KEY_VAULT" --name "$name" --query value -o tsv --only-show-errors 2>/dev/null || true)"
    [ -n "$val" ] && { printf '%s' "$val"; return 0; }
    sleep 10
  done
  return 1
}

APP_SECRET_KEY="$(kv app-secret-key)"        || { echo "FATAL: app-secret-key unreadable from $AGENTIC_KEY_VAULT (RBAC propagation / access?)"; exit 1; }
GH_ID="$(kv github-oauth-client-id)"         || { echo "FATAL: github-oauth-client-id unreadable from $AGENTIC_KEY_VAULT"; exit 1; }
GH_SECRET="$(kv github-oauth-client-secret)" || { echo "FATAL: github-oauth-client-secret unreadable from $AGENTIC_KEY_VAULT"; exit 1; }
DB_PW="$(kv db-app-password)"                || { echo "FATAL: db-app-password unreadable from $AGENTIC_KEY_VAULT"; exit 1; }

DB_URL="postgresql://${AGENTIC_PG_USER:-agentic}:${DB_PW}@${AGENTIC_PG_HOST}:5432/${AGENTIC_PG_DB:-agentic_company}?sslmode=require"

tmp="$(mktemp "$SHARED/.env.XXXXXX")"
chmod 0600 "$tmp"
{
  printf 'APP_SECRET_KEY=%s\n' "$APP_SECRET_KEY"
  printf 'GITHUB_OAUTH_CLIENT_ID=%s\n' "$GH_ID"
  printf 'GITHUB_OAUTH_CLIENT_SECRET=%s\n' "$GH_SECRET"
  printf 'AGENTIC_DATABASE_URL=%s\n' "$DB_URL"
  grep -vE '^[[:space:]]*(#|$)' "$NONSECRET"   # append the non-secret config verbatim
} > "$tmp"
mv -f "$tmp" "$ENV_OUT"; tmp=""
echo "rendered $ENV_OUT from Key Vault $AGENTIC_KEY_VAULT"
