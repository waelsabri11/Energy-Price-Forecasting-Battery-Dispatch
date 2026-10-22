output "acr_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "mlflow_url" {
  value = "https://${azurerm_container_app.mlflow.ingress[0].fqdn}"
}

output "api_url" {
  value = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "resource_group" {
  value = azurerm_resource_group.this.name
}
