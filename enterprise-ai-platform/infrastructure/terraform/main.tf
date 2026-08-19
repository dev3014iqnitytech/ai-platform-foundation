# Terraform main entry — Azure infrastructure for EATAP
# Provisions: AKS, Azure OpenAI, Azure AI Search, PostgreSQL, Redis, Key Vault

terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.90"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.47"
    }
  }
  backend "azurerm" {
    resource_group_name  = "eatap-tfstate-rg"
    storage_account_name = "eataptfstate"
    container_name       = "tfstate"
    key                  = "prod.terraform.tfstate"
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }
}

# ─── Variables ───────────────────────────────────────────────

variable "environment" {
  type    = string
  default = "prod"
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "resource_group_name" {
  type    = string
  default = "eatap-prod-rg"
}

variable "aks_node_count" {
  type    = number
  default = 5
}

variable "aks_node_vm_size" {
  type    = string
  default = "Standard_D8s_v5"
}

# ─── Resource Group ───────────────────────────────────────────

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags = {
    Environment = var.environment
    Project     = "EATAP"
    ManagedBy   = "Terraform"
  }
}

# ─── Azure Key Vault ─────────────────────────────────────────

resource "azurerm_key_vault" "main" {
  name                = "eatap-${var.environment}-kv"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  soft_delete_retention_days = 90
  purge_protection_enabled   = true

  network_acls {
    bypass         = "AzureServices"
    default_action = "Deny"
    ip_rules       = []
  }
}

data "azurerm_client_config" "current" {}

# ─── AKS Cluster ─────────────────────────────────────────────

resource "azurerm_kubernetes_cluster" "main" {
  name                = "eatap-${var.environment}-aks"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = "eatap-${var.environment}"
  kubernetes_version  = "1.29"

  default_node_pool {
    name                = "system"
    node_count          = 3
    vm_size             = "Standard_D4s_v5"
    os_disk_size_gb     = 50
    type                = "VirtualMachineScaleSets"
    enable_auto_scaling = false
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }

  tags = azurerm_resource_group.main.tags
}

resource "azurerm_kubernetes_cluster_node_pool" "app" {
  name                  = "app"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = var.aks_node_vm_size
  node_count            = var.aks_node_count
  enable_auto_scaling   = true
  min_count             = 3
  max_count             = 20
  os_disk_size_gb       = 100
  node_labels = {
    "workload" = "application"
  }
}

# ─── Log Analytics ───────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "main" {
  name                = "eatap-${var.environment}-laws"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 90
}

# ─── PostgreSQL Flexible Server ──────────────────────────────

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "eatap-${var.environment}-pg"
  location               = azurerm_resource_group.main.location
  resource_group_name    = azurerm_resource_group.main.name
  version                = "16"
  administrator_login    = "eatap_admin"
  administrator_password = random_password.pg_password.result
  zone                   = "1"
  storage_mb             = 65536
  sku_name               = "GP_Standard_D4s_v3"

  high_availability {
    mode                      = "ZoneRedundant"
    standby_availability_zone = "2"
  }

  backup_retention_days        = 35
  geo_redundant_backup_enabled = true
}

resource "random_password" "pg_password" {
  length  = 32
  special = true
}

resource "azurerm_postgresql_flexible_server_database" "eatap" {
  name      = "eatap"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# ─── Redis Cache ─────────────────────────────────────────────

resource "azurerm_redis_cache" "main" {
  name                = "eatap-${var.environment}-redis"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  capacity            = 2
  family              = "C"
  sku_name            = "Standard"
  enable_non_ssl_port = false
  minimum_tls_version = "1.2"

  redis_configuration {
    maxmemory_policy = "allkeys-lru"
  }
}

# ─── Outputs ─────────────────────────────────────────────────

output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.main.vault_uri
}

output "postgresql_fqdn" {
  value     = azurerm_postgresql_flexible_server.main.fqdn
  sensitive = true
}

output "redis_hostname" {
  value     = azurerm_redis_cache.main.hostname
  sensitive = true
}
