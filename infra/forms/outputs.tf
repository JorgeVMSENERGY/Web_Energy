output "route53_zone_id" {
  description = "Zona publica donde se publicaran los registros de SES."
  value       = data.aws_route53_zone.site.zone_id
}

output "ses_identity_arn" {
  description = "ARN de la identidad de correo de los formularios."
  value       = aws_sesv2_email_identity.forms.arn
}

output "ses_identity_domain" {
  description = "Dominio verificado para el envio de notificaciones."
  value       = aws_sesv2_email_identity.forms.email_identity
}

output "ses_dkim_tokens" {
  description = "Tokens generados por SES para publicar los registros DKIM."
  value       = aws_sesv2_email_identity.forms.dkim_signing_attributes[0].tokens
}
output "ses_dkim_records" {
  description = "Registros CNAME publicados para Easy DKIM."

  value = [
    for record in aws_route53_record.ses_dkim : {
      name  = record.fqdn
      type  = record.type
      value = one(record.records)
    }
  ]
}

output "ses_mail_from_domain" {
  description = "Dominio MAIL FROM utilizado para SPF y rebotes."
  value       = local.ses_mail_from_domain
}