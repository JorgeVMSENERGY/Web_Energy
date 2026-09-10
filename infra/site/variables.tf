variable "aws_region" {
  description = "Región principal de la infraestructura web."
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

variable "site_bucket_name" {
  description = "Nombre global del bucket privado con el contenido web."
  type        = string
  default     = "vmsenergy-web-prod-787140332697"
}