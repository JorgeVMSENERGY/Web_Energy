import base64
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

ENVIRONMENT = os.environ.get("ENVIRONMENT", "staging")
TABLE_NAME = os.environ["TABLE_NAME"]
ATTACHMENT_BUCKET = os.environ["ATTACHMENT_BUCKET"]
NOTIFICATION_QUEUE_URL = os.environ["NOTIFICATION_QUEUE_URL"]
UPLOAD_URL_TTL_SECONDS = int(os.environ.get("UPLOAD_URL_TTL_SECONDS", "900"))

MAX_FILES = 3
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_FILE_BYTES = 10 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
LEAD_RETENTION_SECONDS = 365 * 24 * 60 * 60

FORM_TYPES = {
    "quick_lead",
    "contact",
    "cta",
    "careers",
    "suggestions",
    "supplier",
    "more_info",
    "stand_rim",
}

ALLOWED_EXTENSIONS = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
    ".csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
}

EMAIL_KEYS = {"email", "correo", "correo_electronico", "f_mail", "mail"}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FIELD_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(TABLE_NAME)
S3 = boto3.client("s3")
SQS = boto3.client("sqs")


class RequestError(Exception):
    def __init__(self, status_code, code, message):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def lambda_handler(event, _context):
    try:
        route_key = event.get("routeKey", "")
        request_context = event.get("requestContext", {})
        http = request_context.get("http", {})
        method = http.get("method", "")
        path = http.get("path", "")

        if route_key == "GET /health" or (method == "GET" and path.endswith("/health")):
            return response(200, {"status": "ok", "environment": ENVIRONMENT})

        payload = parse_json_body(event)

        if route_key == "POST /uploads/presign" or (
            method == "POST" and path.endswith("/uploads/presign")
        ):
            return response(200, create_upload_urls(payload))

        if route_key == "POST /submissions" or (
            method == "POST" and path.endswith("/submissions")
        ):
            return response(202, create_submission(payload, event))

        raise RequestError(404, "route_not_found", "Ruta no disponible.")
    except RequestError as exc:
        return response(exc.status_code, {"error": exc.code, "message": exc.message})
    except ClientError:
        LOGGER.exception("AWS operation failed")
        return response(500, {"error": "service_error", "message": "No fue posible procesar la solicitud."})
    except Exception:
        LOGGER.exception("Unexpected receiver error")
        return response(500, {"error": "internal_error", "message": "No fue posible procesar la solicitud."})


def parse_json_body(event):
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RequestError(400, "invalid_body", "El contenido no es valido.") from exc

    if len(raw_body.encode("utf-8")) > MAX_BODY_BYTES:
        raise RequestError(413, "body_too_large", "La solicitud excede el tamano permitido.")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RequestError(400, "invalid_json", "El contenido JSON no es valido.") from exc

    if not isinstance(payload, dict):
        raise RequestError(400, "invalid_json", "El contenido debe ser un objeto JSON.")
    return payload


def create_upload_urls(payload):
    form_type = validate_form_type(payload.get("form_type"))
    validate_honeypot(payload)
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise RequestError(400, "files_required", "Debes seleccionar al menos un archivo.")
    if len(files) > MAX_FILES:
        raise RequestError(400, "too_many_files", f"Solo se permiten {MAX_FILES} archivos.")

    validated_files = [validate_file_metadata(item) for item in files]
    total_size = sum(item["size"] for item in validated_files)
    if total_size > MAX_TOTAL_FILE_BYTES:
        raise RequestError(400, "files_too_large", "Los archivos exceden 10 MiB en total.")

    upload_session_id = str(uuid.uuid4())
    uploads = []
    for item in validated_files:
        stored_name = f"{uuid.uuid4()}-{safe_filename(item['name'])}"
        object_key = f"pending/{upload_session_id}/{stored_name}"
        upload_url = S3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": ATTACHMENT_BUCKET,
                "Key": object_key,
                "ContentType": item["content_type"],
            },
            ExpiresIn=UPLOAD_URL_TTL_SECONDS,
        )
        uploads.append(
            {
                "name": item["name"],
                "key": object_key,
                "size": item["size"],
                "content_type": item["content_type"],
                "upload_url": upload_url,
                "headers": {"Content-Type": item["content_type"]},
            }
        )

    LOGGER.info(
        "Created upload session",
        extra={"environment": ENVIRONMENT, "form_type": form_type, "file_count": len(uploads)},
    )
    return {
        "upload_session_id": upload_session_id,
        "expires_in": UPLOAD_URL_TTL_SECONDS,
        "uploads": uploads,
    }


def create_submission(payload, event):
    form_type = validate_form_type(payload.get("form_type"))
    validate_honeypot(payload)
    validate_elapsed_time(payload)

    if payload.get("privacy_accepted") is not True:
        raise RequestError(400, "privacy_required", "Debes aceptar el aviso de privacidad.")

    fields = validate_fields(payload.get("fields"))
    visitor_email = find_visitor_email(fields)
    if not visitor_email:
        raise RequestError(400, "email_required", "Debes proporcionar un correo electronico valido.")

    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        raise RequestError(400, "invalid_attachments", "La lista de archivos no es valida.")
    if len(attachments) > MAX_FILES:
        raise RequestError(400, "too_many_files", f"Solo se permiten {MAX_FILES} archivos.")
    if form_type == "careers" and not attachments:
        raise RequestError(400, "cv_required", "Debes adjuntar tu CV.")

    upload_session_id = payload.get("upload_session_id")
    if attachments and not is_uuid(upload_session_id):
        raise RequestError(400, "invalid_upload_session", "La sesion de archivos no es valida.")

    submission_id = str(uuid.uuid4())
    stored_attachments = finalize_attachments(
        attachments, upload_session_id, submission_id, form_type
    )

    now = int(time.time())
    created_at = datetime.now(timezone.utc).isoformat()
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})

    item = {
        "submission_id": submission_id,
        "environment": ENVIRONMENT,
        "form_type": form_type,
        "created_at": created_at,
        "expires_at": now + LEAD_RETENTION_SECONDS,
        "notification_status": "PENDING",
        "visitor_email": visitor_email,
        "fields": fields,
        "attachments": stored_attachments,
        "page_url": limited_string(payload.get("page_url"), 2048),
        "page_title": limited_string(payload.get("page_title"), 256),
        "language": limited_string(payload.get("language"), 16),
        "source_ip": limited_string(http.get("sourceIp"), 64),
        "user_agent": limited_string(http.get("userAgent"), 512),
    }

    TABLE.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(submission_id)",
    )

    try:
        SQS.send_message(
            QueueUrl=NOTIFICATION_QUEUE_URL,
            MessageBody=json.dumps({"submission_id": submission_id}),
        )
    except ClientError:
        TABLE.update_item(
            Key={"submission_id": submission_id},
            UpdateExpression="SET notification_status = :status",
            ExpressionAttributeValues={":status": "QUEUE_ERROR"},
        )
        raise

    LOGGER.info(
        "Accepted form submission",
        extra={"submission_id": submission_id, "environment": ENVIRONMENT, "form_type": form_type},
    )
    return {"status": "received", "submission_id": submission_id}


def finalize_attachments(attachments, upload_session_id, submission_id, form_type):
    if not attachments:
        return []

    expected_prefix = f"pending/{upload_session_id}/"
    validated = []
    total_size = 0

    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise RequestError(400, "invalid_attachment", "Un archivo no es valido.")

        metadata = validate_file_metadata(attachment)
        object_key = attachment.get("key")
        if not isinstance(object_key, str) or not object_key.startswith(expected_prefix):
            raise RequestError(400, "invalid_attachment_key", "La referencia del archivo no es valida.")

        try:
            head = S3.head_object(Bucket=ATTACHMENT_BUCKET, Key=object_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                raise RequestError(400, "attachment_not_uploaded", "Un archivo no termino de subir.") from exc
            raise

        actual_size = int(head.get("ContentLength", 0))
        actual_type = normalize_content_type(head.get("ContentType", ""))
        if actual_size != metadata["size"] or actual_type != metadata["content_type"]:
            raise RequestError(400, "attachment_mismatch", "Los datos del archivo no coinciden.")

        total_size += actual_size
        validated.append((metadata, object_key))

    if total_size > MAX_TOTAL_FILE_BYTES:
        raise RequestError(400, "files_too_large", "Los archivos exceden 10 MiB en total.")

    stored = []
    for metadata, source_key in validated:
        destination_key = (
            f"submissions/{submission_id}/{uuid.uuid4()}-{safe_filename(metadata['name'])}"
        )
        S3.copy_object(
            Bucket=ATTACHMENT_BUCKET,
            Key=destination_key,
            CopySource={"Bucket": ATTACHMENT_BUCKET, "Key": source_key},
            Metadata={"submission-id": submission_id, "form-type": form_type},
            MetadataDirective="REPLACE",
            ContentType=metadata["content_type"],
        )
        S3.delete_object(Bucket=ATTACHMENT_BUCKET, Key=source_key)
        stored.append(
            {
                "key": destination_key,
                "name": metadata["name"],
                "size": metadata["size"],
                "content_type": metadata["content_type"],
            }
        )
    return stored


def validate_form_type(value):
    if value not in FORM_TYPES:
        raise RequestError(400, "invalid_form_type", "El tipo de formulario no es valido.")
    return value


def validate_honeypot(payload):
    if str(payload.get("website") or "").strip():
        raise RequestError(400, "invalid_submission", "La solicitud no es valida.")


def validate_elapsed_time(payload):
    value = payload.get("elapsed_ms")
    if value is None:
        return
    if not isinstance(value, int) or value < 800:
        raise RequestError(400, "submitted_too_fast", "La solicitud fue enviada demasiado rapido.")


def validate_fields(value):
    if not isinstance(value, dict) or not value or len(value) > 40:
        raise RequestError(400, "invalid_fields", "Los campos enviados no son validos.")

    result = {}
    total_length = 0
    for key, raw_value in value.items():
        if not isinstance(key, str) or not FIELD_KEY_PATTERN.fullmatch(key):
            raise RequestError(400, "invalid_field_name", "Un nombre de campo no es valido.")
        if isinstance(raw_value, bool):
            normalized = "Si" if raw_value else "No"
        elif isinstance(raw_value, (str, int, float)):
            normalized = str(raw_value).strip()
        elif raw_value is None:
            normalized = ""
        else:
            raise RequestError(400, "invalid_field_value", "Un valor de campo no es valido.")

        if len(normalized) > 5000:
            raise RequestError(400, "field_too_long", "Un campo excede el tamano permitido.")
        total_length += len(normalized)
        result[key] = normalized

    if total_length > 20000:
        raise RequestError(400, "fields_too_large", "Los campos exceden el tamano permitido.")
    return result


def find_visitor_email(fields):
    for key, value in fields.items():
        normalized_key = key.lower().replace("-", "_")
        if normalized_key in EMAIL_KEYS and EMAIL_PATTERN.fullmatch(value):
            return value.lower()
    return None


def validate_file_metadata(value):
    if not isinstance(value, dict):
        raise RequestError(400, "invalid_file", "Los datos de un archivo no son validos.")

    name = value.get("name")
    size = value.get("size")
    content_type = normalize_content_type(value.get("content_type"))
    if not isinstance(name, str) or not name.strip() or len(name) > 180:
        raise RequestError(400, "invalid_filename", "El nombre de un archivo no es valido.")
    if not isinstance(size, int) or size < 1 or size > MAX_FILE_BYTES:
        raise RequestError(400, "invalid_file_size", "Cada archivo debe pesar como maximo 5 MiB.")

    extension = os.path.splitext(name.lower())[1]
    if extension not in ALLOWED_EXTENSIONS or content_type not in ALLOWED_EXTENSIONS[extension]:
        guessed_type = normalize_content_type(mimetypes.guess_type(name)[0])
        if guessed_type not in ALLOWED_EXTENSIONS.get(extension, set()):
            raise RequestError(400, "invalid_file_type", "El tipo de archivo no esta permitido.")
        content_type = guessed_type

    return {"name": name.strip(), "size": size, "content_type": content_type}


def normalize_content_type(value):
    if not isinstance(value, str):
        return ""
    return value.split(";", 1)[0].strip().lower()


def safe_filename(value):
    basename = os.path.basename(value).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename)
    return cleaned[:160] or "archivo"


def is_uuid(value):
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def limited_string(value, maximum):
    return str(value or "").strip()[:maximum]


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
