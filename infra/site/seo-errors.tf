# Neither error mapping is enabled by default. 403 conversion requires its own
# authorization; it can hide real S3/OAC/WAF access failures.
variable "seo_custom_404_enabled" {
  description = "Enable the custom 404 response only after 404.html is deployed and verified."
  type        = bool
  default     = false
}

variable "seo_map_s3_403_to_404" {
  description = "Separately authorized global 403-to-404 mapping; never required for legacy routing."
  type        = bool
  default     = false

  validation {
    condition     = !var.seo_map_s3_403_to_404 || var.seo_custom_404_enabled
    error_message = "403 conversion requires the custom 404 page to be enabled and verified first."
  }
}
