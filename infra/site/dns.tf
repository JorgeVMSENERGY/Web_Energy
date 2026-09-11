locals {
  dns_zone_name = "vmsenergy.com"

  dns_ipv4_records = {
    autoconfig = "50.6.152.244"
    checador   = "158.69.135.130"
    dev        = "216.246.46.152"
    localhost  = "127.0.0.1"
    mail       = "50.6.152.244"
    webdisk    = "50.6.152.244"
    whm        = "50.6.152.244"
    "www.dev"  = "216.246.46.152"

    # Estos servicios actualmente siguen alojados en Bluehost.
    cpanel  = "50.6.152.244"
    ftp     = "50.6.152.244"
    webmail = "50.6.152.244"
  }

  dns_cname_records = {
    autodiscover = "autodiscover.outlook.com."
    imap         = "mail.vmsenergy.com."
    pop          = "mail.vmsenergy.com."
    smtp         = "mail.vmsenergy.com."
  }
}

resource "aws_route53_zone" "site" {
  name    = local.dns_zone_name
  comment = "Zona DNS pública de VMS Energy"

  tags = {
    Component = "DNS"
  }
}

resource "aws_route53_record" "ipv4" {
  for_each = local.dns_ipv4_records

  zone_id = aws_route53_zone.site.zone_id
  name    = "${each.key}.${local.dns_zone_name}"
  type    = "A"
  ttl     = 3600
  records = [each.value]
}

resource "aws_route53_record" "cname" {
  for_each = local.dns_cname_records

  zone_id = aws_route53_zone.site.zone_id
  name    = "${each.key}.${local.dns_zone_name}"
  type    = "CNAME"
  ttl     = 3600
  records = [each.value]
}

resource "aws_route53_record" "site_ipv4" {
  for_each = toset([
    local.dns_zone_name,
    "www.${local.dns_zone_name}"
  ])

  zone_id = aws_route53_zone.site.zone_id
  name    = each.value
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "site_ipv6" {
  for_each = toset([
    local.dns_zone_name,
    "www.${local.dns_zone_name}"
  ])

  zone_id = aws_route53_zone.site.zone_id
  name    = each.value
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.site.domain_name
    zone_id                = aws_cloudfront_distribution.site.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "mail_exchange" {
  zone_id = aws_route53_zone.site.zone_id
  name    = local.dns_zone_name
  type    = "MX"
  ttl     = 3600

  records = [
    "0 vmsenergy-com.mail.protection.outlook.com."
  ]
}

resource "aws_route53_record" "root_txt" {
  zone_id = aws_route53_zone.site.zone_id
  name    = local.dns_zone_name
  type    = "TXT"
  ttl     = 3600

  records = [
    "google-site-verification=x2lfPRz2PuiKX1dk4NGWiWQ7D_bJwc3K-hiQhqKhP3g",
    "v=spf1 include:spf.protection.outlook.com include:spf.jetsmtp.net -all"
  ]
}

resource "aws_route53_record" "default_dkim" {
  zone_id = aws_route53_zone.site.zone_id
  name    = "default._domainkey.${local.dns_zone_name}"
  type    = "TXT"
  ttl     = 3600

  records = [
    "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAp+SXMrNqCZygKdld+dCgZ5wP/rcGqpdDCREUZrvIodLn2qBv696lGJmPpmjK6pLaxtM89kJsvrquIIgGc68xZC3xjbBCH1PyufZXTNI3XeccUPK2N/hd3tca0HB9ryE/zNp4dcNWTu\"\"Zxrvl+B7DcU/p8ZVfnJ/kSeyuuUZxfcyBAToBTmwIqDt86YExguqxg6p2Z13aElTEMgtQJu2LhajTeKMFDrfB3X7xaFXUtFRzR+gmO9AzUD0iiXTF3/RIlzi15yPhzSIsMzg3cnAJU1+uBV7gA80O7PtWybVr/QVyiR+3txV9FOkFND9YAxVdiRM23TErlcXg1SOY0YQ\"\"/x4QIDAQAB;"
  ]
}

resource "aws_route53_record" "acm_validation" {
  for_each = {
    for option in aws_acm_certificate.site.domain_validation_options :
    option.domain_name => {
      name   = option.resource_record_name
      type   = option.resource_record_type
      record = option.resource_record_value
    }
  }

  allow_overwrite = true
  zone_id         = aws_route53_zone.site.zone_id
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
}

output "route53_zone_id" {
  description = "Identificador de la zona pública de Route 53."
  value       = aws_route53_zone.site.zone_id
}

output "route53_name_servers" {
  description = "Nameservers que posteriormente se configurarán en Bluehost."
  value       = aws_route53_zone.site.name_servers
}