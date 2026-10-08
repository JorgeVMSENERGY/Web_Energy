locals {
  forms_permissions_boundary_arn = "arn:aws:iam::${var.aws_account_id}:policy/VMSWebFormsExecutionBoundary"
}

data "archive_file" "receiver" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/receiver"
  output_path = "${path.module}/.terraform/forms-receiver.zip"
  excludes    = ["__pycache__"]
}

data "archive_file" "notifier" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/notifier"
  output_path = "${path.module}/.terraform/forms-notifier.zip"
  excludes    = ["__pycache__"]
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_cloudwatch_log_group" "receiver" {
  for_each = local.forms_environments

  name              = "/aws/lambda/vmsenergy-forms-${each.key}-receiver"
  retention_in_days = 30

  tags = {
    Component   = "Receiver-Logs"
    Environment = each.key
  }
}

resource "aws_cloudwatch_log_group" "notifier" {
  for_each = local.forms_environments

  name              = "/aws/lambda/vmsenergy-forms-${each.key}-notifier"
  retention_in_days = 30

  tags = {
    Component   = "Notifier-Logs"
    Environment = each.key
  }
}

resource "aws_iam_role" "receiver" {
  for_each = local.forms_environments

  name                 = "vmsenergy-forms-${each.key}-receiver"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume_role.json
  permissions_boundary = local.forms_permissions_boundary_arn

  tags = {
    Component   = "Receiver-Lambda"
    Environment = each.key
  }
}

resource "aws_iam_role" "notifier" {
  for_each = local.forms_environments

  name                 = "vmsenergy-forms-${each.key}-notifier"
  assume_role_policy   = data.aws_iam_policy_document.lambda_assume_role.json
  permissions_boundary = local.forms_permissions_boundary_arn

  tags = {
    Component   = "Notifier-Lambda"
    Environment = each.key
  }
}

data "aws_iam_policy_document" "receiver" {
  for_each = local.forms_environments

  statement {
    sid = "WriteLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.receiver[each.key].arn}:*"
    ]
  }

  statement {
    sid = "UseLeadTable"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem"
    ]

    resources = [
      aws_dynamodb_table.leads[each.key].arn
    ]
  }

  statement {
    sid = "UseAttachments"

    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:PutObject",
      "s3:DeleteObject"
    ]

    resources = [
      "${aws_s3_bucket.attachments[each.key].arn}/*"
    ]
  }

  statement {
    sid = "QueueNotification"

    actions = [
      "sqs:SendMessage"
    ]

    resources = [
      aws_sqs_queue.notifications[each.key].arn
    ]
  }
}

resource "aws_iam_role_policy" "receiver" {
  for_each = local.forms_environments

  name   = "vmsenergy-forms-${each.key}-receiver"
  role   = aws_iam_role.receiver[each.key].id
  policy = data.aws_iam_policy_document.receiver[each.key].json
}

data "aws_iam_policy_document" "notifier" {
  for_each = local.forms_environments

  statement {
    sid = "WriteLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.notifier[each.key].arn}:*"
    ]
  }

  statement {
    sid = "UseLeadTable"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:UpdateItem"
    ]

    resources = [
      aws_dynamodb_table.leads[each.key].arn
    ]
  }

  statement {
    sid = "ReadAttachments"

    actions = [
      "s3:GetObject"
    ]

    resources = [
      "${aws_s3_bucket.attachments[each.key].arn}/*"
    ]
  }

  statement {
    sid = "ConsumeNotifications"

    actions = [
      "sqs:GetQueueAttributes",
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:ChangeMessageVisibility"
    ]

    resources = [
      aws_sqs_queue.notifications[each.key].arn
    ]
  }

  statement {
    sid = "SendEmail"

    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail"
    ]

    resources = [
      aws_sesv2_email_identity.forms.arn
    ]
  }
}

resource "aws_iam_role_policy" "notifier" {
  for_each = local.forms_environments

  name   = "vmsenergy-forms-${each.key}-notifier"
  role   = aws_iam_role.notifier[each.key].id
  policy = data.aws_iam_policy_document.notifier[each.key].json
}

resource "aws_lambda_function" "receiver" {
  for_each = local.forms_environments

  function_name = "vmsenergy-forms-${each.key}-receiver"
  description   = "Recibe y conserva formularios web de VMS Energy (${each.key})."
  role          = aws_iam_role.receiver[each.key].arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = data.archive_file.receiver.output_path
  source_code_hash = data.archive_file.receiver.output_base64sha256

  memory_size                    = 256
  timeout                        = 30
  reserved_concurrent_executions = 2

  environment {
    variables = {
      ENVIRONMENT            = each.key
      TABLE_NAME             = aws_dynamodb_table.leads[each.key].name
      ATTACHMENT_BUCKET      = aws_s3_bucket.attachments[each.key].id
      NOTIFICATION_QUEUE_URL = aws_sqs_queue.notifications[each.key].url
      UPLOAD_URL_TTL_SECONDS = "900"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.receiver,
    aws_iam_role_policy.receiver
  ]

  tags = {
    Component   = "Receiver-Lambda"
    Environment = each.key
  }
}

resource "aws_lambda_function" "notifier" {
  for_each = local.forms_environments

  function_name = "vmsenergy-forms-${each.key}-notifier"
  description   = "Envia notificaciones de formularios mediante SES (${each.key})."
  role          = aws_iam_role.notifier[each.key].arn
  handler       = "app.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = data.archive_file.notifier.output_path
  source_code_hash = data.archive_file.notifier.output_base64sha256

  memory_size                    = 512
  timeout                        = 60
  reserved_concurrent_executions = 1

  environment {
    variables = {
      ENVIRONMENT           = each.key
      TABLE_NAME            = aws_dynamodb_table.leads[each.key].name
      ATTACHMENT_BUCKET     = aws_s3_bucket.attachments[each.key].id
      SENDER_EMAIL          = var.forms_sender_email
      CONTACT_RECIPIENT     = var.contact_recipient
      SUGGESTIONS_RECIPIENT = var.suggestions_recipient
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.notifier,
    aws_iam_role_policy.notifier
  ]

  tags = {
    Component   = "Notifier-Lambda"
    Environment = each.key
  }
}

resource "aws_lambda_event_source_mapping" "notifications" {
  for_each = local.forms_environments

  event_source_arn = aws_sqs_queue.notifications[each.key].arn
  function_name    = aws_lambda_function.notifier[each.key].arn
  enabled          = true
  batch_size       = 1

  function_response_types = [
    "ReportBatchItemFailures"
  ]
}

output "receiver_function_names" {
  description = "Funciones que reciben los formularios web."

  value = {
    for environment, function in aws_lambda_function.receiver :
    environment => function.function_name
  }
}

output "notifier_function_names" {
  description = "Funciones que envian las notificaciones por correo."

  value = {
    for environment, function in aws_lambda_function.notifier :
    environment => function.function_name
  }
}
