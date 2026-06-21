// Fresh-VM stack: network (NSG + static IP) + an Ubuntu VM with a system-assigned
// Managed Identity. Deploy this FIRST for a brand-new environment, then run
// infra/provision.sh against the new VM to add Key Vault + Postgres (main.bicep).
@description('Environment short name: dev | staging | prod')
param env string
param location string = resourceGroup().location
@description('SSH PUBLIC key for the admin user (azureuser)')
param adminSshPublicKey string
@description('Source IP/CIDR allowed to SSH (your IP). Tighten for prod.')
param sshAllowedIp string = '*'
param vmSize string = 'Standard_B2ms'
@description('cloud-init customData (base64) running host-setup.sh on first boot; empty = none')
param customDataBase64 string = ''

module network 'modules/network.bicep' = {
  name: 'network-${env}'
  params: { location: location, env: env, sshAllowedIp: sshAllowedIp }
}

module vmmod 'modules/vm.bicep' = {
  name: 'vm-${env}'
  params: {
    location: location
    env: env
    nicId: network.outputs.nicId
    adminSshPublicKey: adminSshPublicKey
    vmSize: vmSize
    customDataBase64: customDataBase64
  }
}

output publicIp string = network.outputs.publicIp
output vmName string = vmmod.outputs.vmName
output principalId string = vmmod.outputs.principalId
