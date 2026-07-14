"""Cliente de almacenamiento contra Cloudflare R2 (API S3-compatible).

Se usa boto3 apuntando a `R2_ENDPOINT_URL` en vez de S3 real. Solo se firman URLs
localmente (no hay I/O de red al generar un presigned URL), por eso no hace falta
un cliente async pese a que el resto del proyecto es multi-tenant/sync.
"""
import logging
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig

from app.core.config import settings

logger = logging.getLogger(__name__)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


@lru_cache
def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4", region_name="auto"),
    )


def build_object_key(tenant_schema: str, folder: str, extension: str) -> str:
    import uuid

    return f"{tenant_schema}/{folder}/{uuid.uuid4().hex}.{extension}"


def generate_presigned_put_url(key: str, content_type: str) -> str:
    return get_r2_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.R2_PRESIGN_EXPIRE_SECONDS,
    )


def public_url_for(key: str) -> str:
    return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"


def key_from_public_url(url: str) -> str | None:
    """Extrae la key de un `public_url_for(...)` previamente emitido, o None si
    la URL no pertenece al bucket público configurado (p.ej. una URL vieja de
    Supabase u otra fuente externa)."""
    base = f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/"
    if not url.startswith(base):
        return None
    return url[len(base):]


def delete_object(key: str) -> None:
    """Borrado best-effort: nunca levanta, solo loguea si falla."""
    try:
        get_r2_client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    except Exception:
        logger.exception("No se pudo borrar el objeto '%s' en R2", key)
