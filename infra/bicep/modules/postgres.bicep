// Azure Database for PostgreSQL Flexible Server for one environment: managed backups +
// PITR + enforced TLS, public-access firewalled to the VM's public IP.
param location string
@description('Flexible Server name (globally unique, lowercase)')
param serverName string
param databaseName string = 'agentic_company'
param administratorLogin string = 'agentic'
@secure()
param administratorPassword string
@description('Public IP allowed through the server firewall (the VM)')
param allowedIp string
@description('Burstable SKU; B1ms is the cheap MVP default')
param skuName string = 'Standard_B1ms'
param storageSizeGB int = 32
param backupRetentionDays int = 7

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: { name: skuName, tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: { storageSizeGB: storageSizeGB }
    backup: { backupRetentionDays: backupRetentionDays, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

resource allowVm 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'allow-vm'
  properties: { startIpAddress: allowedIp, endIpAddress: allowedIp }
}

output fqdn string = pg.properties.fullyQualifiedDomainName
output databaseName string = database.name
