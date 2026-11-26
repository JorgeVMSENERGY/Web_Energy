# Redirecciones 301 (URLs del sitio WordPress anterior) y www -> sin www.
# Candidata generada: functions/unified-viewer-request.js. La referencia histórica
# functions/redirects.js se conserva sin activar su comportamiento dormido.
resource "aws_cloudfront_function" "unified_viewer_request" {
  name    = "VMSWebUnifiedViewerRequest"
  runtime = "cloudfront-js-2.0"
  comment = "24 approved legacy redirects, www canonical host and LIVE English rewrite"
  publish = var.seo_unified_routing_enabled
  code    = file("${path.module}/functions/unified-viewer-request.js")
}

# False: create/test DEVELOPMENT only, retaining the existing English LIVE ARN.
# True: future reviewed apply publishes and associates the unified candidate.
variable "seo_unified_routing_enabled" {
  description = "Publish and associate the unified function only after approved DEVELOPMENT tests."
  type        = bool
  default     = false
}

locals {
  english_index_rewrite_arn = "arn:aws:cloudfront::${var.aws_account_id}:function/VMSWebEnglishIndexRewrite"
}

# Keep functions/redirects.js unchanged as dormant historical reference.
# VMSWebEnglishIndexRewrite remains unmanaged and available for rollback.
