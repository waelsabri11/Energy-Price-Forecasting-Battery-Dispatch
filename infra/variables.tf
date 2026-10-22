variable "subscription_id" {
  type        = string
  description = "Azure subscription to deploy into."
}

variable "location" {
  type        = string
  default     = "francecentral"
  description = "Azure region."
}

variable "prefix" {
  type        = string
  default     = "enfc"
  description = "Short name prefix (lowercase letters/digits) used for all resources."
}

variable "entsoe_api_key" {
  type        = string
  sensitive   = true
  description = "ENTSO-E Transparency Platform API token. Stored in Key Vault; never commit it."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the inference image pushed to the ACR."
}

variable "inference_cron" {
  type        = string
  default     = "30 9 * * *"
  description = "UTC cron for the daily job. Must finish before the 12:00 CET day-ahead gate closure."
}

variable "postgres_admin_password" {
  type        = string
  sensitive   = true
  description = "Admin password for the MLflow metadata database."
}

variable "tags" {
  type    = map(string)
  default = { project = "energy-forecasting" }
}
