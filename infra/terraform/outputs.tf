output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "container_apps_environment_id" {
  value = azurerm_container_app_environment.main.id
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "eventhub_namespace_fqdn" {
  value = "${azurerm_eventhub_namespace.main.name}.servicebus.windows.net"
}
