// Key Vault for one environment's platform secrets + a role assignment letting the
// VM's system-assigned managed identity read secret VALUES (Key Vault Secrets User).
@description('Azure region')
param location string
@description('Key Vault name (globally unique, 3-24 chars)')
param vaultName string
@description('Object (principal) id of the VM system-assigned managed identity')
param principalId string
@description('Purge protection (irreversible once on). Keep true for staging/prod; dev may set false to allow fast name reuse.')
param enablePurgeProtection bool = true
param tenantId string = subscription().tenantId

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
  }
}

// Built-in role: Key Vault Secrets User (get/list secret values).
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, principalId, kvSecretsUserRoleId)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output vaultName string = kv.name
output vaultUri string = kv.properties.vaultUri
