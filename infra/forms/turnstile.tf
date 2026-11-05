variable "turnstile_enabled" {
  description = "Server-side security flag per environment; production requires separate authorization."
  type        = map(bool)
  default     = { staging = true, prod = false }

  validation {
    condition     = length(var.turnstile_enabled) == 2 && contains(keys(var.turnstile_enabled), "staging") && contains(keys(var.turnstile_enabled), "prod")
    error_message = "Provide independent staging and prod flags."
  }

  validation {
    condition     = !try(var.turnstile_enabled["prod"], true)
    error_message = "Production Turnstile is locked off until its widget and deployment are authorized."
  }
}

locals {
  turnstile_hostname = { staging = "jorgevmsenergy.github.io", prod = "" }
}

# Metadata only. Populate raw SecretString through Secrets Manager, outside Terraform.
# No secret version resource or secret value data source may be added here.
resource "aws_secretsmanager_secret" "turnstile" {
  for_each = { for environment in local.forms_environments : environment => environment if var.turnstile_enabled[environment] }

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
