#!/usr/bin/env bash
# Render /opt/agentic-company/shared/.env from Azure Key Vault using the VM's
# system-assigned MANAGED IDENTITY (no secret needed to fetch secrets). Runs as a
# systemd oneshot BEFORE the console service, and again on each deploy so a rotated
# secret is picked up. Secrets are NEVER echoed. On any failure the LAST-GOOD .env is
# kept (we render to a temp file and mv only on success).
set -euo pipefail
umask 077

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/agentic-company}"
SHARED="$DEPLOY_ROOT/shared"
ENV_OUT="$SHARED/.env"
NONSECRET="${NONSECRET_FILE:-$SHARED/env.nonsecret}"

[ -f "$NONSECRET" ] || { echo "FATAL: missing non-secret config $NONSECRET"; exit 1; }
# shellcheck disable=SC1090
. "$NONSECRET"   # AGENTIC_KEY_VAULT, AGENTIC_PG_HOST/DB/USER, AGENTIC_WEB_*, callback, redis, codex, profile
: "${AGENTIC_KEY_VAULT:?set AGENTIC_KEY_VAULT in $NONSECRET}"
: "${AGENTIC_PG_HOST:?set AGENTIC_PG_HOST in $NONSECRET}"

# Managed-identity login, with a short retry while IMDS warms up at boot.
for i in 1 2 3 4 5; do
  if az login --identity --only-show-errors >/dev/null 2>&1; then break; fi
  [ "$i" = 5 ] && { echo "FATAL: az login --identity failed (managed identity / IMDS not ready)"; exit 1; }
  sleep 3
done

kv() { az keyvault secret show --vault-name "$AGENTIC_KEY_VAULT" --name "$1" --query value -o tsv --only-show-errors; }

APP_SECRET_KEY="$(kv app-secret-key)"
GH_ID="$(kv github-oauth-client-id)"
GH_SECRET="$(kv github-oauth-client-secret)"
DB_PW="$(kv db-app-password)"
[ -n "$APP_SECRET_KEY" ] && [ -n "$DB_PW" ] || { echo "FATAL: a required secret came back empty from $AGENTIC_KEY_VAULT"; exit 1; }

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
mv -f "$tmp" "$ENV_OUT"
echo "rendered $ENV_OUT from Key Vault $AGENTIC_KEY_VAULT"
