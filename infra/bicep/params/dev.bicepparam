using '../main.bicep'

param env = 'dev'
// dev may disable purge protection for fast vault name reuse during iteration.
param keyVaultPurgeProtection = false

// Dynamic values are supplied by infra/provision.sh via environment variables
// (the VM's managed-identity principal id, the VM public IP, and the generated DB password).
param vmPrincipalId = readEnvironmentVariable('VM_PRINCIPAL_ID')
param vmPublicIp = readEnvironmentVariable('VM_PUBLIC_IP')
param dbAdminPassword = readEnvironmentVariable('DB_ADMIN_PASSWORD')
