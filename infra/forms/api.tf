locals {
  forms_allowed_origins = {
    staging = [
      "https://jorgevmsenergy.github.io"
    ]

    prod = [
      "https://vmsenergy.com",
      "https://www.vmsenergy.com"
    ]
  }
}

resource "aws_apigatewayv2_api" "forms" {
  for_each = local.forms_environments

  name          = "vmsenergy-forms-${each.key}"
  description   = "API publica para formularios web de VMS Energy (${each.key})."
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = false

    allow_headers = [
      "content-type"
    ]

    allow_methods = [
      "GET",
      "POST",
      "OPTIONS"
    ]

    allow_origins = local.forms_allowed_origins[each.key]
    max_age       = 3600
  }

  tags = {
    Component   = "Forms-API"
    Environment = each.key
  }
}

resource "aws_apigatewayv2_integration" "receiver" {
  for_each = local.forms_environments

  api_id                 = aws_apigatewayv2_api.forms[each.key].id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.receiver[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}

resource "aws_apigatewayv2_route" "health" {
  for_each = local.forms_environments

  api_id    = aws_apigatewayv2_api.forms[each.key].id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.receiver[each.key].id}"
}

resource "aws_apigatewayv2_route" "uploads_presign" {
  for_each = local.forms_environments

  api_id    = aws_apigatewayv2_api.forms[each.key].id
  route_key = "POST /uploads/presign"
  target    = "integrations/${aws_apigatewayv2_integration.receiver[each.key].id}"
}

resource "aws_apigatewayv2_route" "submissions" {
  for_each = local.forms_environments

  api_id    = aws_apigatewayv2_api.forms[each.key].id
  route_key = "POST /submissions"
  target    = "integrations/${aws_apigatewayv2_integration.receiver[each.key].id}"
}

resource "aws_apigatewayv2_stage" "default" {
  for_each = local.forms_environments

  api_id      = aws_apigatewayv2_api.forms[each.key].id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = false
    throttling_burst_limit   = 5
    throttling_rate_limit    = 2
  }

  tags = {
    Component   = "Forms-API-Stage"
    Environment = each.key
  }
}

resource "aws_lambda_permission" "api_gateway_receiver" {
  for_each = local.forms_environments

  statement_id  = "AllowFormsApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.receiver[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.forms[each.key].execution_arn}/*/*"
}

resource "aws_s3_bucket_cors_configuration" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id

  cors_rule {
    id = "browser-form-uploads"

    allowed_headers = [
      "*"
    ]

    allowed_methods = [
      "PUT",
      "HEAD"
    ]

    allowed_origins = local.forms_allowed_origins[each.key]

    expose_headers = [
      "ETag"
    ]

    max_age_seconds = 3600
  }
}