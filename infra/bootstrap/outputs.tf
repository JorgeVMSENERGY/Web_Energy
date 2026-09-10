output "terraform_state_bucket_name" {
  description = "Nombre del bucket de estado de Terraform."
  value       = aws_s3_bucket.terraform_state.id
}

output "terraform_state_bucket_arn" {
  description = "ARN del bucket de estado de Terraform."
  value       = aws_s3_bucket.terraform_state.arn
}

output "terraform_state_bucket_region" {
  description = "Región del bucket de estado de Terraform."
  value       = aws_s3_bucket.terraform_state.region
}