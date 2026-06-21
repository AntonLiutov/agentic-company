// Per-environment secret + DB plane: Key Vault (+ MI role assignment) and a Postgres
// Flexible Server. Deploy with provision.sh, which supplies the dynamic params
// (vmPrincipalId, vmPublicIp, dbAdminPassword) on the CLI.
@description('Environment short name: dev | staging | prod')
param env string
param location string = resourceGroup().location
@description('Object (principal) id of the VM system-assigned managed identity')
param vmPrincipalId string
@description('VM public IP to allow through the Postgres firewall')
param vmPublicIp string
@secure()
@description('Postgres admin password (also seeded into Key Vault as db-app-password)')
param dbAdminPassword string
@description('Key Vault purge protection — true for staging/prod, may be false for dev')
param keyVaultPurgeProtection bool = true

var vaultName = 'kv-agentic-${env}'
var pgServerName = 'pg-agentic-${env}'

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
    location: location
    serverName: pgServerName
    administratorPassword: dbAdminPassword
    allowedIp: vmPublicIp
  }
}

output vaultName string = keyvault.outputs.vaultName
output pgFqdn string = postgres.outputs.fqdn
