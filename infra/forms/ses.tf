resource "aws_sesv2_email_identity" "forms" {
  email_identity = var.ses_identity_domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }

  tags = {
    Component = "Transactional-Email"
  }
}
locals {
  ses_mail_from_domain = "bounce.${var.ses_identity_domain}"
  ses_dkim_tokens      = aws_sesv2_email_identity.forms.dkim_signing_attributes[0].tokens
}

resource "aws_route53_record" "ses_dkim" {
  count = 3

  zone_id = data.aws_route53_zone.site.zone_id
  name    = "${local.ses_dkim_tokens[count.index]}._domainkey.${var.ses_identity_domain}"
  type    = "CNAME"
  ttl     = 300

  records = [
    "${local.ses_dkim_tokens[count.index]}.${var.ses_dkim_signing_hosted_zone}."
  ]
}

resource "aws_route53_record" "ses_mail_from_mx" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = local.ses_mail_from_domain
  type    = "MX"
  ttl     = 300

  records = [
    "10 feedback-smtp.${var.aws_region}.amazonses.com."
  ]
}

resource "aws_route53_record" "ses_mail_from_spf" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = local.ses_mail_from_domain
  type    = "TXT"
  ttl     = 300

  records = [
    "v=spf1 include:amazonses.com ~all"
  ]
}

resource "aws_sesv2_email_identity_mail_from_attributes" "forms" {
  email_identity = aws_sesv2_email_identity.forms.email_identity

  behavior_on_mx_failure = "REJECT_MESSAGE"
  mail_from_domain       = local.ses_mail_from_domain

  depends_on = [
    aws_route53_record.ses_mail_from_mx,
    aws_route53_record.ses_mail_from_spf
  ]
}