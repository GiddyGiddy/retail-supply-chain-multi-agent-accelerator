variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "location" {
  description = "Azure region for the deployment."
  type        = string
  default     = "eastus"
}

variable "name_prefix" {
  description = "Short lowercase prefix used to name resources."
  type        = string
  default     = "retailflow"
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

variable "tags" {
  type    = map(string)
  default = { application = "retail-agentic-supply-chain" }
}
