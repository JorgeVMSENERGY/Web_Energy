resource "aws_acm_certificate" "site" {
  domain_name = "vmsenergy.com"

  subject_alternative_names = [
    "www.vmsenergy.com"
  ]

  validation_method = "DNS"
  key_algorithm     = "RSA_2048"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Component = "TLS"
  }
}

output "acm_certificate_arn" {
  description = "ARN del certificado TLS para el dominio público."
  value       = aws_acm_certificate.site.arn
}

output "acm_dns_validation_records" {
  description = "Registros DNS requeridos para validar el certificado ACM."

  value = {
    for option in aws_acm_certificate.site.domain_validation_options :
    option.domain_name => {
      name  = option.resource_record_name
      type  = option.resource_record_type
      value = option.resource_record_value
    }
  }
}
resource "aws_acm_certificate_validation" "site" {
  certificate_arn = aws_acm_certificate.site.arn

  validation_record_fqdns = [
    for option in aws_acm_certificate.site.domain_validation_options :
    option.resource_record_name
  ]
}