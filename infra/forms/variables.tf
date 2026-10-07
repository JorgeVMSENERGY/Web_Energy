variable "aws_region" {
  description = "Region principal del backend de formularios."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Perfil local de AWS IAM Identity Center."
  type        = string
  default     = "vms-web-infra"
}

variable "aws_account_id" {
  description = "Cuenta AWS autorizada para este proyecto."
  type        = string
  default     = "787140332697"
}

variable "root_domain" {
  description = "Dominio publico principal."
  type        = string
  default     = "vmsenergy.com"
}

variable "ses_identity_domain" {
  description = "Subdominio usado exclusivamente para enviar notificaciones."
  type        = string
  default     = "forms.vmsenergy.com"
}
variable "ses_dkim_signing_hosted_zone" {
  description = "Zona de firma DKIM devuelta por SES para esta identidad."
  type        = string
  default     = "dkim.amazonses.com"
}
variable "forms_sender_email" {
  description = "Direccion verificada utilizada por SES como remitente."
  type        = string
  default     = "notificaciones@forms.vmsenergy.com"
}

variable "contact_recipient" {
  description = "Destinatario de contacto, cotizaciones y carreras."
  type        = string
  default     = "contacto@vmsenergy.com"
}

variable "suggestions_recipient" {
  description = "Buzon principal de quejas y sugerencias."
  type        = string
  default     = "quejasysugerencias@vmsenergy.com"
}