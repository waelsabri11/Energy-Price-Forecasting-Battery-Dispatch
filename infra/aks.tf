# Optional AKS demo cluster (off by default). One small node, no autoscaler, free control-plane tier.
# Runs the manifests in ../k8s; images come from the same ACR.
variable "enable_aks" {
  type        = bool
  default     = false
  description = "Create a 1-node AKS cluster to run the Kubernetes manifests on Azure."
}

variable "aks_node_size" {
  type        = string
  default     = "Standard_D2as_v7"
  description = "VM size of the single AKS node."
}

resource "azurerm_kubernetes_cluster" "this" {
  count               = var.enable_aks ? 1 : 0
  name                = "aks-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  dns_prefix          = "aks-${local.name}"
  sku_tier            = "Free"
  tags                = var.tags

  default_node_pool {
    name                 = "system"
    vm_size              = var.aks_node_size
    node_count           = 1
    auto_scaling_enabled = false
    os_disk_size_gb      = 64
  }

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  count                = var.enable_aks ? 1 : 0
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.this[0].kubelet_identity[0].object_id
}

output "aks_name" {
  value = var.enable_aks ? azurerm_kubernetes_cluster.this[0].name : null
}
