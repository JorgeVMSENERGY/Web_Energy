output "site_bucket_name" {
  description = "Nombre del bucket privado con el contenido web."
  value       = aws_s3_bucket.site.id
}

output "site_bucket_arn" {
  description = "ARN del bucket privado."
  value       = aws_s3_bucket.site.arn
}

output "site_bucket_regional_domain_name" {
  description = "Dominio regional utilizado posteriormente por CloudFront."
  value       = aws_s3_bucket.site.bucket_regional_domain_name
}

output "cloudfront_distribution_id" {
  description = "Identificador de la distribución CloudFront."
  value       = aws_cloudfront_distribution.site.id
}

output "cloudfront_distribution_arn" {
  description = "ARN de la distribución CloudFront."
  value       = aws_cloudfront_distribution.site.arn
}

output "cloudfront_domain_name" {
  description = "Dominio temporal para probar la página antes del DNS."
  value       = aws_cloudfront_distribution.site.domain_name
}