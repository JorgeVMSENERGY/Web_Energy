locals {
  forms_environments = toset([
    "staging",
    "prod"
  ])

  attachment_retention_days = 90
}

resource "aws_dynamodb_table" "leads" {
  for_each = local.forms_environments

  name         = "vmsenergy-forms-${each.key}-leads"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "submission_id"

  attribute {
    name = "submission_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = each.key == "prod"

  tags = {
    Component   = "Lead-Storage"
    Environment = each.key
  }
}

resource "aws_s3_bucket" "attachments" {
  for_each = local.forms_environments

  bucket = "vmsenergy-forms-attachments-${each.key}-${var.aws_account_id}"

  tags = {
    Component   = "Attachments"
    Environment = each.key
  }
}

resource "aws_s3_bucket_ownership_controls" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }

    bucket_key_enabled = false
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id

  rule {
    id     = "expire-form-attachments"
    status = "Enabled"

    filter {}

    expiration {
      days = local.attachment_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [
    aws_s3_bucket_ownership_controls.attachments
  ]
}

data "aws_iam_policy_document" "attachments" {
  for_each = local.forms_environments

  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = [
      "s3:*"
    ]

    resources = [
      aws_s3_bucket.attachments[each.key].arn,
      "${aws_s3_bucket.attachments[each.key].arn}/*"
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "attachments" {
  for_each = local.forms_environments

  bucket = aws_s3_bucket.attachments[each.key].id
  policy = data.aws_iam_policy_document.attachments[each.key].json

  depends_on = [
    aws_s3_bucket_public_access_block.attachments
  ]
}