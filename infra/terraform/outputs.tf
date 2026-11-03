output "resource_group_name" {
  value = data.azurerm_resource_group.main.name
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "api_url" {
  value = local.api_url
}

output "web_url" {
  value = local.web_url
}

output "container_app_api" {
  value = azurerm_container_app.api.name
}

output "container_app_web" {
  value = azurerm_container_app.web.name
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "eventhub_namespace_fqdn" {
  value = "${azurerm_eventhub_namespace.main.name}.servicebus.windows.net"
}
