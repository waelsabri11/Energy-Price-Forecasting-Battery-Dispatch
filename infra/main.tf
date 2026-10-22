data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name = "${var.prefix}${random_string.suffix.result}"
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = var.tags
}

# ---------- storage: forecast outputs (Azure Files mount) + MLflow artifacts (blob) ----------
resource "azurerm_storage_account" "this" {
  name                            = "st${local.name}"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = var.tags
}

resource "azurerm_storage_share" "outputs" {
  name               = "forecasts"
  storage_account_id = azurerm_storage_account.this.id
  quota              = 5
}

resource "azurerm_storage_share" "mlflow" {
  name               = "mlflow-artifacts"
  storage_account_id = azurerm_storage_account.this.id
  quota              = 20
}

# ---------- registry, identity, secrets ----------
resource "azurerm_container_registry" "this" {
  name                = "acr${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = var.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_key_vault" "this" {
  name                       = "kv-${local.name}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  tags                       = var.tags
}

resource "azurerm_role_assignment" "kv_admin" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "kv_reader" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_key_vault_secret" "entsoe" {
  name         = "entsoe-api-key"
  value        = var.entsoe_api_key
  key_vault_id = azurerm_key_vault.this.id
  depends_on   = [azurerm_role_assignment.kv_admin]
}

# ---------- MLflow metadata DB ----------
resource "azurerm_postgresql_flexible_server" "mlflow" {
  name                          = "pg-${local.name}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  version                       = "16"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  administrator_login           = "mlflow"
  administrator_password        = var.postgres_admin_password
  public_network_access_enabled = true
  zone                          = "1"
  tags                          = var.tags
}

resource "azurerm_postgresql_flexible_server_database" "mlflow" {
  name      = "mlflow"
  server_id = azurerm_postgresql_flexible_server.mlflow.id
}

# allow Azure-internal services (the Container Apps environment) to reach the DB
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.mlflow.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------- Container Apps ----------
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  tags                       = var.tags
}

resource "azurerm_container_app_environment_storage" "outputs" {
  name                         = "outputs"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.this.name
  share_name                   = azurerm_storage_share.outputs.name
  access_key                   = azurerm_storage_account.this.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app_environment_storage" "mlflow" {
  name                         = "mlflowartifacts"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.this.name
  share_name                   = azurerm_storage_share.mlflow.name
  access_key                   = azurerm_storage_account.this.primary_access_key
  access_mode                  = "ReadWrite"
}

# MLflow tracking server + registry (public image build pushed to the ACR by CI/CD)
resource "azurerm_container_app" "mlflow" {
  name                         = "ca-mlflow-${local.name}"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name  = "pg-uri"
    value = "postgresql://mlflow:${var.postgres_admin_password}@${azurerm_postgresql_flexible_server.mlflow.fqdn}:5432/mlflow?sslmode=require"
  }

  ingress {
    external_enabled = false # internal only: MLflow has no auth, reachable from inside the environment
    target_port      = 5000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 1

    volume {
      name         = "artifacts"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.mlflow.name
    }

    container {
      name    = "mlflow"
      image   = "${azurerm_container_registry.this.login_server}/energy-mlflow:${var.image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["/bin/sh", "-c"]
      args = [
        "mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri $PG_URI --artifacts-destination /mlflow/artifacts --serve-artifacts"
      ]

      env {
        name        = "PG_URI"
        secret_name = "pg-uri"
      }

      volume_mounts {
        name = "artifacts"
        path = "/mlflow/artifacts"
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}

# Daily inference job: forecast + dispatch for D+1, written to the Azure Files share
resource "azurerm_container_app_job" "daily_inference" {
  name                         = "caj-infer-${local.name}"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  container_app_environment_id = azurerm_container_app_environment.this.id
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1
  tags                         = var.tags

  schedule_trigger_config {
    cron_expression          = var.inference_cron
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "entsoe-api-key"
    key_vault_secret_id = azurerm_key_vault_secret.entsoe.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  template {
    volume {
      name         = "outputs"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.outputs.name
    }

    container {
      name   = "infer"
      image  = "${azurerm_container_registry.this.login_server}/energy-forecasting:${var.image_tag}"
      cpu    = 1
      memory = "2Gi"
      args   = ["infer", "--model-uri", "models:/fr-dayahead-price-quantiles/latest"]

      env {
        name        = "ENTSOE_API_KEY"
        secret_name = "entsoe-api-key"
      }
      env {
        name  = "MLFLOW_TRACKING_URI"
        value = "https://${azurerm_container_app.mlflow.ingress[0].fqdn}"
      }
      env {
        name  = "OUTPUT_DIR"
        value = "/outputs"
      }

      volume_mounts {
        name = "outputs"
        path = "/outputs"
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull, azurerm_role_assignment.kv_reader]
}

# Read-only API for the latest forecast (scales to zero)
resource "azurerm_container_app" "api" {
  name                         = "ca-api-${local.name}"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1

    volume {
      name         = "outputs"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.outputs.name
    }

    container {
      name   = "api"
      image  = "${azurerm_container_registry.this.login_server}/energy-forecasting:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"
      args   = ["serve"]

      env {
        name  = "OUTPUT_DIR"
        value = "/outputs"
      }

      volume_mounts {
        name = "outputs"
        path = "/outputs"
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull]
}
