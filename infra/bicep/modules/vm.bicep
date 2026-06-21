// Ubuntu VM with a system-assigned Managed Identity (so it can read its Key Vault).
param location string
param env string
param nicId string
param vmSize string = 'Standard_B2ms'
param adminUsername string = 'azureuser'
@description('SSH PUBLIC key for the admin user')
param adminSshPublicKey string
@description('cloud-init customData (base64) to run host-setup on first boot; empty = none')
param customDataBase64 string = ''
var prefix = 'agentic-${env}'

resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: '${prefix}-vm'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: '${prefix}-vm'
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            { path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: adminSshPublicKey }
          ]
        }
      }
      customData: empty(customDataBase64) ? null : customDataBase64
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-jammy'
        sku: '22_04-lts-gen2'
        version: 'latest'
      }
      osDisk: { createOption: 'FromImage', managedDisk: { storageAccountType: 'Premium_LRS' } }
    }
    networkProfile: { networkInterfaces: [ { id: nicId } ] }
  }
}

output principalId string = vm.identity.principalId
output vmName string = vm.name
