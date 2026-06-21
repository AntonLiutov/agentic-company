#!/usr/bin/env bash
# Provision (idempotently) one environment's secret + DB plane in Azure. Run from the
# OPERATOR's machine after `az login` + `az account set --subscription <id>`.
#
#   infra/provision.sh <env> <resource-group> <vm-name> [vm-resource-group]
#
# Ensures the VM has a system-assigned managed identity, deploys the Bicep (Key Vault +
# role assignment + Postgres Flexible Server), generates the DB password ONCE and seeds
# it (plus app-secret-key + the GitHub OAuth pair) into the vault. Secrets are generated
# locally and pushed straight to Key Vault — never printed.
set -euo pipefail

ENV="${1:?env, e.g. dev}"
RG="${2:?resource group for Key Vault + Postgres}"
VM_NAME="${3:?VM name (for its managed identity + public IP)}"
VM_RG="${4:-$RG}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAM="$HERE/bicep/params/$ENV.bicepparam"
[ -f "$PARAM" ] || { echo "FATAL: no param file $PARAM — copy params/dev.bicepparam for this env."; exit 1; }

echo ">>> ensure the VM has a system-assigned managed identity"
PRINCIPAL_ID="$(az vm identity show -g "$VM_RG" -n "$VM_NAME" --query principalId -o tsv 2>/dev/null || true)"
if [ -z "$PRINCIPAL_ID" ] || [ "$PRINCIPAL_ID" = "null" ]; then
  PRINCIPAL_ID="$(az vm identity assign -g "$VM_RG" -n "$VM_NAME" --query systemAssignedIdentity -o tsv)"
fi
echo "    MI principalId=$PRINCIPAL_ID"

echo ">>> VM public IP (for the Postgres firewall rule)"
VM_IP="$(az vm list-ip-addresses -g "$VM_RG" -n "$VM_NAME" \
  --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' -o tsv)"
[ -n "$VM_IP" ] || { echo "FATAL: could not resolve the VM public IP."; exit 1; }
echo "    VM public IP=$VM_IP"

echo ">>> DB password (idempotent: reuse a prior local copy so a re-run does NOT rotate the live credential)"
STATE="$HERE/.provision-state"; mkdir -p "$STATE"; chmod 700 "$STATE"
PWFILE="$STATE/$ENV.dbpw"
if [ -f "$PWFILE" ]; then
  DB_PW="$(cat "$PWFILE")"
  echo "    reusing existing DB password from $PWFILE"
else
  # token_urlsafe + a fixed tail guarantees Azure PG's 3-of-4 complexity (upper/lower/digit/special).
  DB_PW="$(python -c 'import secrets; print(secrets.token_urlsafe(28) + "Aa1-")')"
  ( umask 077; printf '%s' "$DB_PW" > "$PWFILE" )
  echo "    generated + stored DB password at $PWFILE (gitignored)"
fi

echo ">>> deploy Bicep (Key Vault + role assignment + Postgres) for env=$ENV"
# The .bicepparam reads these dynamic values via readEnvironmentVariable() and resolves
# its own template through `using '../main.bicep'`.
export VM_PRINCIPAL_ID="$PRINCIPAL_ID" VM_PUBLIC_IP="$VM_IP" DB_ADMIN_PASSWORD="$DB_PW"
OUT="$(az deployment group create \
  --resource-group "$RG" \
  --parameters "$PARAM" \
  --query 'properties.outputs' -o json)"
unset DB_ADMIN_PASSWORD
VAULT="$(printf '%s' "$OUT" | python -c 'import sys,json;print(json.load(sys.stdin)["vaultName"]["value"])')"
PG_FQDN="$(printf '%s' "$OUT" | python -c 'import sys,json;print(json.load(sys.stdin)["pgFqdn"]["value"])')"
echo "    vault=$VAULT  pg=$PG_FQDN"

echo ">>> seed secrets into $VAULT (enter this env's GitHub OAuth app credentials)"
read -r -p "GitHub OAuth client id: " GH_ID
read -r -s -p "GitHub OAuth client secret: " GH_SECRET; echo
"$HERE/../ops/deploy/seed-secrets.sh" "$VAULT" "$DB_PW" "$GH_ID" "$GH_SECRET"
unset DB_PW GH_SECRET

cat <<EOF

================================================================================
DONE for env=$ENV
  Key Vault : $VAULT
  Postgres  : $PG_FQDN
NEXT:
  1. Set in ops/deploy/env.nonsecret.$ENV:  AGENTIC_KEY_VAULT=$VAULT  and  AGENTIC_PG_HOST=$PG_FQDN
     (and this env's GITHUB_OAUTH_CALLBACK_URL), commit it.
  2. On the VM: sudo bash ops/deploy/bootstrap_vm.sh
  3. Confirm: sudo -u deployer az login --identity && az keyvault secret list --vault-name $VAULT
================================================================================
EOF
