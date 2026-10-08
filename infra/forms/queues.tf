resource "aws_sqs_queue" "notifications_dlq" {
  for_each = local.forms_environments

  name                      = "vmsenergy-forms-${each.key}-notifications-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = {
    Component   = "Notification-DLQ"
    Environment = each.key
  }
}

resource "aws_sqs_queue" "notifications" {
  for_each = local.forms_environments

  name                       = "vmsenergy-forms-${each.key}-notifications"
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 180
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notifications_dlq[each.key].arn
    maxReceiveCount     = 5
  })

  tags = {
    Component   = "Notification-Queue"
    Environment = each.key
  }
}