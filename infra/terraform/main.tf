data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "main" {
  name = var.resource_group_name
}

locals {
  location = data.azurerm_resource_group.main.location
  # Stable suffix for globally unique names (registry, vault, database, event hubs).
  suffix = substr(md5("${var.subscription_id}${var.resource_group_name}${var.name_prefix}"), 0, 6)

  api_name = "${var.name_prefix}-api"
  web_name = "${var.name_prefix}-web"
  api_url  = "https://${local.api_name}.${azurerm_container_app_environment.main.default_domain}"
  web_url  = "https://${local.web_name}.${azurerm_container_app_environment.main.default_domain}"

  database_url = "postgresql+psycopg://${var.postgres_admin_username}:${urlencode(var.postgres_admin_password)}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${azurerm_postgresql_flexible_server_database.main.name}?sslmode=require"
}

# ---------------------------------------------------------------- observability
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.name_prefix}-logs"
  location            = local.location
  resource_group_name = data.azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "${var.name_prefix}-env"
  location                   = local.location
  resource_group_name        = data.azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags

  lifecycle {
    # Azure adds the default Consumption workload profile itself.
    ignore_changes = [workload_profile]
  }
}

# ---------------------------------------------------------------- registry
resource "azurerm_container_registry" "main" {
  name                = "${var.name_prefix}${local.suffix}"
  resource_group_name = data.azurerm_resource_group.main.name
  location            = local.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

# ---------------------------------------------------------------- identities
resource "azurerm_user_assigned_identity" "api" {
  name                = "${var.name_prefix}-api-id"
  location            = local.location
  resource_group_name = data.azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "web" {
  name                = "${var.name_prefix}-web-id"
  location            = local.location
  resource_group_name = data.azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_role_assignment" "api_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_role_assignment" "web_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.web.principal_id
}

# ---------------------------------------------------------------- database
resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "${var.name_prefix}-pg-${local.suffix}"
  resource_group_name    = data.azurerm_resource_group.main.name
  location               = var.postgres_location
  version                = "16"
  administrator_login    = var.postgres_admin_username
  administrator_password = var.postgres_admin_password
  storage_mb             = 32768
  sku_name               = var.postgres_sku_name
  zone                   = var.postgres_zone == "" ? null : var.postgres_zone
  backup_retention_days  = 7
  tags                   = var.tags

  lifecycle {
    # Azure picks the zone when none is set; don't try to change it afterwards.
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "supply_chain"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# 0.0.0.0 is Azure's "internal services" rule: only Azure-hosted services can connect.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "AllowAzureServices"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------------------------------------------------------------- event streaming
resource "azurerm_eventhub_namespace" "main" {
  name                = "${var.name_prefix}-events-${local.suffix}"
  location            = local.location
  resource_group_name = data.azurerm_resource_group.main.name
  sku                 = "Standard"
  capacity            = 1
  # Entra-only access; shared access keys are disabled.
  local_authentication_enabled = false
  minimum_tls_version          = "1.2"
  tags                         = var.tags
}

resource "azurerm_eventhub" "inventory" {
  name              = "inventory-events"
  namespace_id      = azurerm_eventhub_namespace.main.id
  partition_count   = 2
  message_retention = 1
}

resource "azurerm_eventhub" "decisions" {
  name              = "approval-decisions"
  namespace_id      = azurerm_eventhub_namespace.main.id
  partition_count   = 2
  message_retention = 1
}

resource "azurerm_role_assignment" "api_event_sender" {
  scope                = azurerm_eventhub_namespace.main.id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_role_assignment" "api_event_receiver" {
  scope                = azurerm_eventhub_namespace.main.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# ---------------------------------------------------------------- secrets
resource "azurerm_key_vault" "main" {
  name                       = "kv-${var.name_prefix}-${local.suffix}"
  location                   = local.location
  resource_group_name        = data.azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  tags                       = var.tags
}

resource "azurerm_role_assignment" "deployer_secrets_officer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "api_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

# Role assignments take a moment to propagate before secrets can be written.
resource "time_sleep" "rbac_propagation" {
  create_duration = "60s"
  depends_on      = [azurerm_role_assignment.deployer_secrets_officer, azurerm_role_assignment.api_secrets_user]
}

resource "azurerm_key_vault_secret" "database_url" {
  name         = "database-url"
  value        = local.database_url
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [time_sleep.rbac_propagation]
}

resource "azurerm_key_vault_secret" "openai_api_key" {
  count        = var.openai_api_key == "" ? 0 : 1
  name         = "openai-api-key"
  value        = var.openai_api_key
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [time_sleep.rbac_propagation]
}

# ---------------------------------------------------------------- API
resource "azurerm_container_app" "api" {
  name                         = local.api_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = data.azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_url.versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  dynamic "secret" {
    for_each = azurerm_key_vault_secret.openai_api_key
    content {
      name                = "openai-api-key"
      key_vault_secret_id = secret.value.versionless_id
      identity            = azurerm_user_assigned_identity.api.id
    }
  }

  dynamic "secret" {
    for_each = var.bc_sandbox_verifier == null ? [] : [var.bc_sandbox_verifier]
    content {
      name                = "bc-sandbox-verifier-secret"
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value.secret_name}"
      identity            = azurerm_user_assigned_identity.api.id
    }
  }

  dynamic "secret" {
    for_each = var.bc_purchasing_secret_name == null ? [] : [var.bc_purchasing_secret_name]
    content {
      name                = "bc-purchasing-secret"
      key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/${secret.value}"
      identity            = azurerm_user_assigned_identity.api.id
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas                     = 1
    max_replicas                     = 3
    termination_grace_period_seconds = 30

    http_scale_rule {
      name                = "http-scaler"
      concurrent_requests = "10"
    }

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"

      liveness_probe {
        transport               = "TCP"
        port                    = 8000
        initial_delay           = 0
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
      }
      readiness_probe {
        transport               = "TCP"
        port                    = 8000
        initial_delay           = 0
        interval_seconds        = 5
        timeout                 = 5
        failure_count_threshold = 48
        success_count_threshold = 1
      }
      startup_probe {
        transport               = "TCP"
        port                    = 8000
        initial_delay           = 1
        interval_seconds        = 1
        timeout                 = 3
        failure_count_threshold = 240
      }

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      dynamic "env" {
        for_each = azurerm_key_vault_secret.openai_api_key
        content {
          name        = "OPENAI_API_KEY"
          secret_name = "openai-api-key"
        }
      }
      env {
        name  = "AUTH_ENABLED"
        value = tostring(var.auth_enabled)
      }
      env {
        name  = "ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      env {
        name  = "ENTRA_API_CLIENT_ID"
        value = var.entra_api_client_id
      }
      env {
        name  = "CORS_ORIGINS"
        value = join(",", compact([local.web_url, var.web_custom_domain == "" ? "" : "https://${var.web_custom_domain}"]))
      }
      env {
        name  = "KAFKA_BOOTSTRAP_SERVERS"
        value = "${azurerm_eventhub_namespace.main.name}.servicebus.windows.net:9093"
      }
      env {
        name  = "KAFKA_SECURITY_PROTOCOL"
        value = "SASL_SSL"
      }
      env {
        name  = "KAFKA_SASL_MECHANISM"
        value = "OAUTHBEARER"
      }
      # Tells DefaultAzureCredential which managed identity to use.
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.api.client_id
      }
      env {
        name  = "BC_WRITES_ENABLED"
        value = "true"
      }
      env {
        name  = "BC_DISPATCH_ENABLED"
        value = "true"
      }
      env {
        name  = "BC_SANDBOX_DISPATCH_ENABLED"
        value = "true"
      }
      env {
        name  = "BC_STANDARD_DISPATCH_ENABLED"
        value = "false"
      }
      env {
        name  = "SANDBOX_PROPOSALS_ENABLED"
        value = tostring(var.sandbox_proposals_enabled)
      }
      dynamic "env" {
        for_each = var.bc_purchasing_secret_name == null ? [] : [var.bc_purchasing_secret_name]
        content {
          name        = "BC_CLIENT_SECRET"
          secret_name = "bc-purchasing-secret"
        }
      }
      dynamic "env" {
        for_each = var.bc_sandbox_verifier == null ? {} : {
          BC_TENANT_ID                  = var.bc_sandbox_verifier.tenant_id
          BC_ENVIRONMENT                = var.bc_sandbox_verifier.environment
          BC_COMPANY_ID                 = var.bc_sandbox_verifier.company_id
          BC_CLIENT_ID                  = var.bc_sandbox_verifier.purchasing_client_id
          BC_LOCAL_CURRENCY             = var.bc_sandbox_verifier.local_currency
          BC_SANDBOX_VERIFIER_CLIENT_ID = var.bc_sandbox_verifier.verifier_client_id
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = var.bc_sandbox_verifier == null ? [] : [var.bc_sandbox_verifier]
        content {
          name        = "BC_SANDBOX_VERIFIER_CLIENT_SECRET"
          secret_name = "bc-sandbox-verifier-secret"
        }
      }
    }
  }

  lifecycle {
    # CI/CD owns the running image after the first deployment.
    ignore_changes = [template[0].container[0].image, workload_profile_name]
  }

  depends_on = [azurerm_role_assignment.api_acr_pull, azurerm_role_assignment.api_secrets_user, time_sleep.rbac_propagation]
}

# ---------------------------------------------------------------- frontend
resource "azurerm_container_app" "web" {
  name                         = local.web_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = data.azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.web.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.web.id
  }

  ingress {
    external_enabled = true
    target_port      = 80
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "web"
      image  = var.web_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }

  lifecycle {
    ignore_changes = [template[0].container[0].image, workload_profile_name]
  }

  depends_on = [azurerm_role_assignment.web_acr_pull]
}
