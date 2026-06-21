// Network for a fresh VM: NSG (SSH gated, 80/443 open), VNet/subnet, STATIC public IP, NIC.
param location string
param env string
@description('Source IP/CIDR allowed to SSH (e.g. your home IP). Tighten for prod.')
param sshAllowedIp string = '*'
var prefix = 'agentic-${env}'

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${prefix}-nsg'
  location: location
  properties: {
    securityRules: [
      {
        name: 'AllowSSH'
        properties: {
          priority: 1000, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: sshAllowedIp, sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '22'
        }
      }
      {
        name: 'AllowHTTP'
        properties: {
          priority: 1010, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '80'
        }
      }
      {
        name: 'AllowHTTPS'
        properties: {
          priority: 1020, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.20.0.0/16'] }
    subnets: [
      {
        name: 'default'
        properties: { addressPrefix: '10.20.1.0/24', networkSecurityGroup: { id: nsg.id } }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2023-09-01' = {
  name: '${prefix}-pip'
  location: location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-09-01' = {
  name: '${prefix}-nic'
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: vnet.properties.subnets[0].id }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}

output nicId string = nic.id
output publicIp string = pip.properties.ipAddress
