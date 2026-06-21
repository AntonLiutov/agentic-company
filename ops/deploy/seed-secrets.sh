#!/usr/bin/env bash
# One-time-per-env: seed the platform secrets into this env's Key Vault. Run from the
# OPERATOR's machine (logged in via `az login`), NOT on the VM. Secrets are generated
# locally and pushed straight to Key Vault — never printed, never written to disk.
#
# Usage: ops/deploy/seed-secrets.sh <vault-name> <db-app-password> <gh-client-id> <gh-client-secret>
#   <db-app-password> must equal the password you pass to the Bicep deploy (provision.sh
#   generates one and does both). app-secret-key is generated here and set IMMUTABLY.
set -euo pipefail

VAULT="${1:?vault name, e.g. kv-agentic-dev}"
DB_PW="${2:?db app password (same value used for the Postgres admin password)}"
GH_ID="${3:?GitHub OAuth client id}"
GH_SECRET="${4:?GitHub OAuth client secret}"

set_secret() { az keyvault secret set --vault-name "$VAULT" --name "$1" --value "$2" --only-show-errors >/dev/null; echo "  set $1"; }

# APP_SECRET_KEY: the Fernet ROOT key. Generated ONCE, set IMMUTABLE, never seen again.
# Refuse to overwrite an existing one (overwriting orphans every per-user ciphertext).
if az keyvault secret show --vault-name "$VAULT" --name app-secret-key --only-show-errors >/dev/null 2>&1; then
  echo "app-secret-key already exists in $VAULT — leaving it untouched (it is immutable per env)."
else
  APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  set_secret app-secret-key "$APP_SECRET_KEY"
  unset APP_SECRET_KEY
fi

set_secret db-app-password "$DB_PW"
set_secret github-oauth-client-id "$GH_ID"
set_secret github-oauth-client-secret "$GH_SECRET"
echo "Seeded $VAULT. The VM's managed identity will read these at boot via fetch-secrets.sh."
