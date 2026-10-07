data "aws_route53_zone" "site" {
  name         = var.root_domain
  private_zone = false
}