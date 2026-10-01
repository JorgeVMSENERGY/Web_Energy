# Redirecciones 301 (URLs del sitio WordPress anterior) y www -> sin www.
# Código en functions/redirects.js. Se asocia al default_cache_behavior en cloudfront.tf.
resource "aws_cloudfront_function" "redirects" {
  name    = "vmsenergy-redirects-301"
  runtime = "cloudfront-js-2.0"
  comment = "301 de URLs viejas y www.vmsenergy.com -> vmsenergy.com"
  publish = true
  code    = file("${path.module}/functions/redirects.js")
}
