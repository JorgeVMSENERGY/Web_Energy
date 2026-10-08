import json
import logging
import os
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP

import boto3


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

ENVIRONMENT = os.environ.get("ENVIRONMENT", "staging")
TABLE_NAME = os.environ["TABLE_NAME"]
ATTACHMENT_BUCKET = os.environ["ATTACHMENT_BUCKET"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
CONTACT_RECIPIENT = os.environ["CONTACT_RECIPIENT"]
SUGGESTIONS_RECIPIENT = os.environ["SUGGESTIONS_RECIPIENT"]

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 10 * 1024 * 1024

SUBJECTS = {
    "quick_lead": "Nuevo contacto rapido",
    "contact": "Nueva solicitud de contacto",
    "cta": "Nueva solicitud comercial",
    "careers": "Nueva postulacion de carrera",
    "suggestions": "Nueva queja o sugerencia",
    "supplier": "Nueva solicitud de procura",
    "more_info": "Nueva solicitud de informacion",
    "stand_rim": "Nuevo registro Stand RIM",
}

FIELD_LABELS = {
    "nombre": "Nombre",
    "name": "Nombre",
    "empresa": "Empresa",
    "company": "Empresa",
    "correo": "Correo",
    "email": "Correo",
    "telefono": "Telefono",
    "phone": "Telefono",
    "lada": "Lada",
    "puesto": "Puesto",
    "ciudad": "Ciudad",
    "mensaje": "Mensaje",
    "message": "Mensaje",
    "industria": "Industria",
    "interes": "Interes",
    "area_interes": "Area de interes",
    "valorProyecto": "Valor del proyecto",
    "valor_proyecto": "Valor del proyecto",
    "descripcion_proyecto": "Descripcion del proyecto",
    "categoria": "Categoria",
    "etapa": "Etapa",
    "servicio_catalogo": "Servicio o producto",
    "puesto_interes": "Puesto de interes",
    "linkedin": "LinkedIn",
}

DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(TABLE_NAME)
S3 = boto3.client("s3")
SES = boto3.client("sesv2")


def lambda_handler(event, _context):
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            process_record(record)
        except Exception:
            LOGGER.exception("Notification processing failed", extra={"sqs_message_id": message_id})
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def process_record(record):
    body = json.loads(record.get("body") or "{}")
    submission_id = body.get("submission_id")
    if not isinstance(submission_id, str) or not submission_id:
        raise ValueError("SQS message does not contain submission_id")

    result = TABLE.get_item(
        Key={"submission_id": submission_id},
        ConsistentRead=True,
    )
    item = result.get("Item")
    if not item:
        raise ValueError(f"Submission {submission_id} does not exist")

    if item.get("notification_status") == "SENT":
        LOGGER.info("Submission was already sent", extra={"submission_id": submission_id})
        return

    form_type = item.get("form_type", "")
    if form_type not in SUBJECTS:
        raise ValueError(f"Unsupported form type: {form_type}")

    recipient = (
        SUGGESTIONS_RECIPIENT if form_type == "suggestions" else CONTACT_RECIPIENT
    )

    try:
        message = build_message(item, recipient)
        reply_to = item.get("visitor_email")
        request = {
            "FromEmailAddress": SENDER_EMAIL,
            "Destination": {"ToAddresses": [recipient]},
            "Content": {"Raw": {"Data": message.as_bytes(policy=SMTP)}},
        }
        if isinstance(reply_to, str) and reply_to:
            request["ReplyToAddresses"] = [reply_to]

        ses_result = SES.send_email(**request)
        sent_at = datetime.now(timezone.utc).isoformat()
        TABLE.update_item(
            Key={"submission_id": submission_id},
            UpdateExpression=(
                "SET notification_status = :status, sent_at = :sent_at, "
                "ses_message_id = :message_id REMOVE last_error"
            ),
            ExpressionAttributeValues={
                ":status": "SENT",
                ":sent_at": sent_at,
                ":message_id": ses_result["MessageId"],
            },
        )
        LOGGER.info(
            "Notification sent",
            extra={
                "submission_id": submission_id,
                "environment": ENVIRONMENT,
                "form_type": form_type,
                "ses_message_id": ses_result["MessageId"],
            },
        )
    except Exception as exc:
        mark_retry(submission_id, exc)
        raise


def build_message(item, recipient):
    form_type = item["form_type"]
    submission_id = item["submission_id"]
    environment_prefix = "[STAGING] " if ENVIRONMENT == "staging" else ""
    subject = f"{environment_prefix}[VMS Web] {SUBJECTS[form_type]}"

    message = EmailMessage()
    message["Subject"] = clean_header(subject)
    message["From"] = f"Formularios VMS Energy <{SENDER_EMAIL}>"
    message["To"] = recipient
    if item.get("visitor_email"):
        message["Reply-To"] = clean_header(item["visitor_email"])

    message.set_content(build_plain_text(item))
    add_attachments(message, item.get("attachments") or [])
    return message


def build_plain_text(item):
    fields = item.get("fields") or {}
    lines = [
        "Se recibio un nuevo formulario en el sitio de VMS Energy.",
        "",
        f"Ambiente: {ENVIRONMENT}",
        f"Tipo: {item.get('form_type', '')}",
        f"Identificador: {item.get('submission_id', '')}",
        f"Fecha UTC: {item.get('created_at', '')}",
    ]

    page_url = item.get("page_url")
    if page_url:
        lines.append(f"Pagina: {page_url}")

    lines.extend(["", "Datos enviados:"])
    for key in sorted(fields, key=str.casefold):
        label = FIELD_LABELS.get(key, key.replace("_", " ").replace("-", " ").title())
        value = fields[key]
        lines.append(f"{label}: {value}")

    attachments = item.get("attachments") or []
    if attachments:
        lines.extend(["", "Archivos adjuntos:"])
        for attachment in attachments:
            lines.append(f"- {attachment.get('name', 'archivo')}")

    lines.extend(
        [
            "",
            "Puedes responder directamente a este mensaje para contactar al visitante.",
            "Este correo fue generado automaticamente por el backend de formularios.",
        ]
    )
    return "\n".join(lines)


def add_attachments(message, attachments):
    if len(attachments) > 3:
        raise ValueError("Submission has too many attachments")

    total_size = 0
    for attachment in attachments:
        object_key = attachment.get("key")
        filename = attachment.get("name") or "archivo"
        content_type = attachment.get("content_type") or "application/octet-stream"
        expected_size = int(attachment.get("size") or 0)

        if not isinstance(object_key, str) or not object_key.startswith("submissions/"):
            raise ValueError("Invalid attachment key")
        if expected_size < 1 or expected_size > MAX_ATTACHMENT_BYTES:
            raise ValueError("Invalid attachment size")

        result = S3.get_object(Bucket=ATTACHMENT_BUCKET, Key=object_key)
        data = result["Body"].read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) != expected_size or len(data) > MAX_ATTACHMENT_BYTES:
            raise ValueError("Attachment size does not match stored metadata")

        total_size += len(data)
        if total_size > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("Attachments exceed total size limit")

        if "/" in content_type:
            main_type, sub_type = content_type.split("/", 1)
        else:
            main_type, sub_type = "application", "octet-stream"
        message.add_attachment(
            data,
            maintype=main_type,
            subtype=sub_type,
            filename=filename,
        )


def mark_retry(submission_id, error):
    try:
        TABLE.update_item(
            Key={"submission_id": submission_id},
            UpdateExpression=(
                "SET notification_status = :status, last_attempt_at = :attempt, "
                "last_error = :error ADD notification_attempts :one"
            ),
            ExpressionAttributeValues={
                ":status": "RETRYING",
                ":attempt": datetime.now(timezone.utc).isoformat(),
                ":error": clean_error(error),
                ":one": 1,
            },
        )
    except Exception:
        LOGGER.exception("Could not update retry status", extra={"submission_id": submission_id})


def clean_header(value):
    return str(value).replace("\r", " ").replace("\n", " ").strip()[:240]


def clean_error(error):
    return clean_header(f"{type(error).__name__}: {error}")[:500]
