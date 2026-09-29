"""Cliente de almacenamiento contra Cloudflare R2 (API S3-compatible).

Se usa boto3 apuntando a `R2_ENDPOINT_URL` en vez de S3 real. Solo se firman URLs
localmente (no hay I/O de red al generar un presigned URL), por eso no hace falta
un cliente async pese a que el resto del proyecto es multi-tenant/sync.
"""
import logging
import re
from functools import lru_cache

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


R2_CONNECT_TIMEOUT_SECONDS = 3
R2_READ_TIMEOUT_SECONDS = 5
R2_MAX_ATTEMPTS = 2

# Carpetas del bucket por campo (spec 088). El primer segmento de una key es el
# esquema del negocio dueño; el segundo, una de estas carpetas.
FOLDER_PRODUCTS = "products"
FOLDER_LOGO = "logo"
FOLDER_PAYMENT_METHODS = "payment-methods"
FOLDER_RECEIPTS = "comprobantes"


class AssetKeyError(ValueError):
    """La key no es una key nueva válida para ese negocio y carpeta (spec 088,
    FR-004). Los llamadores la convierten en 422."""


class StorageUnavailable(RuntimeError):
    """R2 no respondió con un "existe" / "no existe" definitivo (spec 088,
    FR-003: fallo cerrado). Los llamadores la convierten en 503."""


@lru_cache
def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        # spec 088 (research D3): timeouts cortos y pocos reintentos para que una
        # verificación de existencia "falle cerrado" en segundos y no cuelgue la
        # petición 60 s (default de boto3). El cliente es compartido, así que
        # `delete_object` (best-effort) también los hereda.
        config=BotoConfig(
            signature_version="s3v4",
            region_name="auto",
            connect_timeout=R2_CONNECT_TIMEOUT_SECONDS,
            read_timeout=R2_READ_TIMEOUT_SECONDS,
            retries={"total_max_attempts": R2_MAX_ATTEMPTS, "mode": "standard"},
        ),
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
    """URL pública contra `R2_PUBLIC_BASE_URL`. Tras la spec 080 solo la usan los
    comprobantes del comensal (`cart/service.py`, carpeta `comprobantes`), que
    quedan fuera de alcance (FR-014). Las imágenes de producto / logo / método de
    pago usan `asset_display_url` (dominio personalizado nuevo)."""
    return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"


# ---------------------------------------------------------------------------
# Referencia de imagen key <-> URL (spec 080). Única fuente de la regla: la
# escritura (normalize_asset_ref), la lectura (asset_display_url), la migración
# de datos y el borrado del objeto anterior (object_key_for_deletion) la
# consumen por igual. Funciones puras, sin I/O.
#
# "Bucket gestionado" = el valor empieza por el dominio personalizado nuevo
# (ASSETS_BASE_URL) o por el R2_PUBLIC_BASE_URL heredado. Reconocer ambos cubre
# FR-004 (normalizar tanto la URL vieja como la que el frontend reenvía del
# dominio nuevo) y FR-009b (tolerancia de lectura de una fila no migrada).
# ---------------------------------------------------------------------------

def _managed_bucket_prefixes() -> tuple[str, ...]:
    """Prefijos de dominio (con `/` final) que identifican el bucket gestionado:
    `ASSETS_BASE_URL` y el `R2_PUBLIC_BASE_URL` heredado, de-duplicados por si un
    entorno los define iguales. El orden pone primero el dominio nuevo."""
    ordered: list[str] = []
    for base in (settings.ASSETS_BASE_URL, settings.R2_PUBLIC_BASE_URL):
        prefix = f"{base.rstrip('/')}/"
        if prefix not in ordered:
            ordered.append(prefix)
    return tuple(ordered)


def _key_if_managed(value: str) -> str | None:
    """Si `value` es una URL absoluta del bucket gestionado, devuelve su key
    (el resto tras el prefijo de dominio, con query string incluido si lo hay);
    si no, None. El match de prefijo es indiferente a mayúsculas en el host
    (Edge Cases del spec); la key se devuelve tal cual porque sus segmentos sí
    son sensibles a mayúsculas."""
    lowered = value.lower()
    for prefix in _managed_bucket_prefixes():
        if lowered.startswith(prefix.lower()):
            return value[len(prefix):]
    return None


def normalize_asset_ref(value: str | None) -> str | None:
    """Valor a **persistir** para una referencia de imagen de producto / logo /
    método de pago (spec 080, FR-002/FR-004):

    - vacío / solo espacios -> ``None``
    - URL absoluta del bucket gestionado (dominio viejo o nuevo) -> su key
    - key -> la misma key
    - URL de otro origen -> intacta (FR-010)

    En base de datos nunca queda esquema ni dominio para el bucket gestionado.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    key = _key_if_managed(trimmed)
    return key if key is not None else trimmed


def asset_display_url(value: str | None) -> str | None:
    """URL de visualización lista para usar de una referencia de imagen (spec
    080, FR-005/FR-006/FR-007). No modifica lo que hay en base de datos: la URL
    absoluta solo existe en la respuesta hacia el consumidor.

    - vacío -> ``None``
    - key, o URL del bucket gestionado (incluida la forma anterior
      ``pub-…r2.dev`` — tolerancia de lectura, FR-009b) -> ``{ASSETS_BASE_URL}/{key}``
    - URL de otro origen -> intacta (FR-010)
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    assets_base = settings.ASSETS_BASE_URL.rstrip("/")
    key = _key_if_managed(trimmed)
    if key is not None:
        return f"{assets_base}/{key}"
    if "://" in trimmed:
        return trimmed
    return f"{assets_base}/{trimmed}"


def object_key_for_deletion(value: str | None) -> str | None:
    """Key del objeto a borrar (best-effort) al reemplazar una imagen (spec 080,
    FR-011/FR-013). Reemplaza al antiguo ``key_from_public_url``: además de una
    URL del bucket gestionado acepta una **key directa**, y devuelve ``None``
    para una URL de otro origen — nunca se intenta borrar un objeto ajeno.

    - vacío -> ``None``
    - key -> la misma key
    - URL del bucket gestionado (dominio viejo o nuevo) -> su key
    - URL de otro origen -> ``None``
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    key = _key_if_managed(trimmed)
    if key is not None:
        return key
    if "://" in trimmed:
        return None
    return trimmed


def delete_object(key: str) -> None:
    """Borrado best-effort: nunca levanta, solo loguea si falla."""
    try:
        get_r2_client().delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    except Exception:
        logger.exception("No se pudo borrar el objeto '%s' en R2", key)


# ---------------------------------------------------------------------------
# Integridad de referencias (spec 088). Validación de key nueva, existencia en
# R2 y key que se puede borrar. `normalize_asset_ref`, `asset_display_url`,
# `object_key_for_deletion` y `delete_object` (arriba) no cambian.
# ---------------------------------------------------------------------------

_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}"


def validate_asset_key(value: str, tenant_schema: str, folder: str) -> str:
    """Devuelve `value` sin modificarlo si es una key **nueva** válida
    (`{esquema}/{carpeta}/{nombre}` del propio negocio, sensible a mayúsculas);
    si no, lanza `AssetKeyError`. Pura: sin I/O y sin normalizar, recortar ni
    decodificar nada — lo que no encaja se rechaza (research D2)."""
    pattern = rf"{re.escape(tenant_schema)}/{re.escape(folder)}/{_NAME_PATTERN}"
    if re.fullmatch(pattern, value) is None or ".." in value:
        raise AssetKeyError("La key no corresponde al negocio o a la carpeta esperada.")
    return value


_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


def object_exists(key: str) -> bool:
    """`HEAD` del objeto en R2. `True` si existe, `False` si R2 responde que no.
    Cualquier otro resultado (403, 5xx, sin conexión, timeout) lanza
    `StorageUnavailable`: no saber no es lo mismo que "no existe" (FR-003)."""
    try:
        get_r2_client().head_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _NOT_FOUND_CODES:
            return False
        logger.warning("R2 respondió %s al verificar '%s'", code, key)
        raise StorageUnavailable(f"R2 respondió {code}") from exc
    except BotoCoreError as exc:
        # Incluye EndpointConnectionError, ConnectTimeoutError y ReadTimeoutError.
        logger.warning("R2 no respondió al verificar '%s': %s", key, exc)
        raise StorageUnavailable(str(exc)) from exc


def deletable_key(value: str | None, tenant_schema: str, folder: str) -> str | None:
    """Key que **se puede** borrar al reemplazar `value`, o `None` (no borrar).
    Compone `object_key_for_deletion` con la convención: una URL de otro origen,
    una key fuera de convención (histórica) o de otro negocio/carpeta nunca se
    borra — queda huérfana, que es seguro (FR-004, FR-005)."""
    key = object_key_for_deletion(value)
    if key is None:
        return None
    try:
        return validate_asset_key(key, tenant_schema, folder)
    except AssetKeyError:
        return None
