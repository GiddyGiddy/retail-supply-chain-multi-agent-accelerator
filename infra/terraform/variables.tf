variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "resource_group_name" {
  description = "Existing resource group to deploy into."
  type        = string
  default     = "rg-inventarmind"
}

variable "name_prefix" {
  description = "Short lowercase alphanumeric prefix used to name resources (max 14 characters)."
  type        = string
  default     = "inventarmind"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,13}$", var.name_prefix))
    error_message = "name_prefix must be 3-14 lowercase letters/digits and start with a letter."
  }
}

variable "postgres_admin_username" {
  description = "Administrator username for PostgreSQL."
  type        = string
}

variable "postgres_admin_password" {
  description = "Administrator password for PostgreSQL. Supply via TF_VAR_postgres_admin_password."
  type        = string
  sensitive   = true
}

variable "postgres_location" {
  description = "Region for PostgreSQL. Burstable SKUs are not available to this subscription in swedencentral, so the database lives in northeurope."
  type        = string
  default     = "northeurope"
}

variable "postgres_sku_name" {
  description = "PostgreSQL Flexible Server SKU."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_zone" {
  description = "Availability zone for PostgreSQL (1, 2 or 3). Empty lets Azure choose."
  type        = string
  default     = ""
}

variable "openai_api_key" {
  description = "Optional OpenAI API key. Leave empty to run the agents in deterministic fallback mode. Supply via TF_VAR_openai_api_key."
  type        = string
  default     = ""
  sensitive   = true
}

variable "auth_enabled" {
  description = "Require Microsoft Entra ID sign-in on the API. Keep true for any internet-facing deployment."
  type        = bool
  default     = true
}

variable "entra_tenant_id" {
  description = "Entra tenant that issues the API access tokens."
  type        = string
  default     = ""
}

variable "entra_api_client_id" {
  description = "Client ID of the Entra app registration that represents the API (token audience)."
  type        = string
  default     = ""
}

variable "api_image" {
  description = "Initial API image. CI/CD replaces it after the first deployment."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "web_image" {
  description = "Initial frontend image. CI/CD replaces it after the first deployment."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "tags" {
  type    = map(string)
  default = { application = "inventarmind" }
}
