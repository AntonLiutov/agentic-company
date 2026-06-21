// Per-environment secret + DB plane: Key Vault (+ MI role assignment) and a Postgres
// Flexible Server. Deploy with provision.sh, which supplies the dynamic params
// (vmPrincipalId, vmPublicIp, dbAdminPassword) on the CLI.
@description('Environment short name: dev | staging | prod')
param env string
param location string = resourceGroup().location
@description('Postgres region — eastus is offer-restricted on PAYG subscriptions; eastus2 works.')
param dbLocation string = 'eastus2'
@description('Object (principal) id of the VM system-assigned managed identity')
param vmPrincipalId string
@description('VM public IP to allow through the Postgres firewall')
param vmPublicIp string
@secure()
@description('Postgres admin password (also seeded into Key Vault as db-app-password)')
param dbAdminPassword string
@description('Key Vault purge protection — true for staging/prod, may be false for dev')
param keyVaultPurgeProtection bool = true

// Globally-unique DNS names — add a stable per-RG suffix to avoid name-taken collisions
// (Key Vault names are tenant-wide unique + capped at 24 chars; PG FQDN is global).
var suffix = substring(uniqueString(resourceGroup().id), 0, 5)
// PG name is region-derived so a failed/reserved deploy in another region can't block it.
var pgSuffix = substring(uniqueString(resourceGroup().id, dbLocation), 0, 5)
var vaultName = 'kv-agentic-${env}-${suffix}'
var pgServerName = 'pg-agentic-${env}-${pgSuffix}'

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault-${env}'
  params: {
    location: location
    vaultName: vaultName
    principalId: vmPrincipalId
    enablePurgeProtection: keyVaultPurgeProtection
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres-${env}'
  params: {
    location: dbLocation
    serverName: pgServerName
    administratorPassword: dbAdminPassword
    allowedIp: vmPublicIp
  }
}

output vaultName string = keyvault.outputs.vaultName
output pgFqdn string = postgres.outputs.fqdn
