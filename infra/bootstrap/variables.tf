variable "aws_region" {
  description = "Región principal para la infraestructura de la página web."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Perfil local de AWS IAM Identity Center."
  type        = string
  default     = "vms-web-infra"
}

variable "aws_account_id" {
  description = "Cuenta de AWS autorizada para este proyecto."
  type        = string
  default     = "787140332697"
}

variable "terraform_state_bucket_name" {
  description = "Nombre global del bucket que almacenará los estados de Terraform."
  type        = string
  default     = "vmsenergy-web-tfstate-787140332697"
}