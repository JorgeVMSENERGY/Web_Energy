resource "aws_route53_record" "ses_dmarc" {
  zone_id = data.aws_route53_zone.site.zone_id
  name    = "_dmarc.${var.ses_identity_domain}"
  type    = "TXT"
  ttl     = 300

  records = [
    "v=DMARC1; p=none; adkim=r; aspf=r; pct=100"
  ]
}