output "lead_table_names" {
  description = "Tablas DynamoDB que conservan los envios recibidos."

  value = {
    for environment, table in aws_dynamodb_table.leads :
    environment => table.name
  }
}

output "attachment_bucket_names" {
  description = "Buckets privados para archivos adjuntos."

  value = {
    for environment, bucket in aws_s3_bucket.attachments :
    environment => bucket.id
  }
}

output "notification_queue_urls" {
  description = "Colas SQS utilizadas para solicitar el envio de correos."

  value = {
    for environment, queue in aws_sqs_queue.notifications :
    environment => queue.url
  }
}

output "notification_dlq_urls" {
  description = "Colas que conservan las notificaciones fallidas."

  value = {
    for environment, queue in aws_sqs_queue.notifications_dlq :
    environment => queue.url
  }
}