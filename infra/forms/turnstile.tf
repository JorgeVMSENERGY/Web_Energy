variable "turnstile_enabled" {
  description = "Server-side security flag per environment; production requires separate authorization."
  type        = map(bool)
  default     = { staging = true, prod = true }

  validation {
    condition     = length(var.turnstile_enabled) == 2 && contains(keys(var.turnstile_enabled), "staging") && contains(keys(var.turnstile_enabled), "prod")
    error_message = "Provide independent staging and prod flags."
  }
}

locals {
  turnstile_hostnames = {
    staging = ["jorgevmsenergy.github.io"]
    prod    = ["vmsenergy.com", "www.vmsenergy.com"]
  }
}

# Metadata only. Populate raw SecretString through Secrets Manager, outside Terraform.
# No secret version resource or secret value data source may be added here.
resource "aws_secretsmanager_secret" "turnstile" {
  # Keep existing resource addresses; provisioning never enables verification.
  for_each = local.forms_environments

  name                    = "vmsenergy/forms/${each.key}/turnstile"
  description             = "Cloudflare Turnstile secret for forms ${each.key}; value managed out of band."
  recovery_window_in_days = 30
  tags = {
    Environment = each.key
    Component   = "Forms-Turnstile"
  }
}

output "turnstile_secret_arns" {
  description = "Secret metadata only; populate values outside Terraform."
  value       = { for environment, secret in aws_secretsmanager_secret.turnstile : environment => secret.arn }
}
