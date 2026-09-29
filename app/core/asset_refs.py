"""Integridad de las referencias a archivos en R2 (spec 088).

Complementa a `app/core/storage.py` (que resuelve lo puro y lo que toca R2) con
lo que además consulta la base de datos o decide un cambio: única fuente de la
regla, igual que `storage.py` lo es de key <-> URL desde la spec 080. Los
servicios existentes (`products`, `tenant`, `sales`, `cart`) solo la invocan.

- `decide_image_change`: qué hacer con una imagen enviada (FR-001/FR-002).
- `ensure_image_exists`: la key nueva debe existir en R2 (FR-003).
- `is_key_referenced` / `asset_key_lock`: el archivo anterior solo se borra si nadie
  más lo usa y sin carrera entre guardar y borrar (FR-006/FR-007).
- `resolve_receipt_key`: el comprobante del comensal es un archivo existente del
  propio negocio (FR-008).
"""
import enum
import logging

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.models import Tenant
from app.core.storage import (
    FOLDER_RECEIPTS,
    AssetKeyError,
    StorageUnavailable,
    _managed_bucket_prefixes,
    normalize_asset_ref,
    object_exists,
    validate_asset_key,
)
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.payment import PaymentMethod
from app.models.product import Product

logger = logging.getLogger(__name__)

MSG_IMAGE_INVALID = "La imagen no es válida para este negocio."
MSG_IMAGE_NOT_FOUND = "La imagen no se encontró en el almacenamiento. Sube el archivo de nuevo."
MSG_STORAGE_UNAVAILABLE = (
    "El almacenamiento de archivos no responde. Intenta de nuevo en unos segundos."
)
MSG_RECEIPT_INVALID = "El comprobante no pertenece a este negocio o no es válido."
MSG_RECEIPT_FOREIGN = "El comprobante debe ser un archivo subido desde la aplicación."


class ImageDecision(str, enum.Enum):
    KEEP = "keep"      # no tocar: nada que validar ni verificar
    IGNORE = "ignore"  # formulario desactualizado: se descarta la imagen en silencio
    APPLY = "apply"    # aplicar la imagen enviada (el llamador verifica existencia)


def is_managed_ref(value: str) -> bool:
    """"Gestionado" = referencia del bucket propio: una key (o una URL del bucket,
    que `normalize_asset_ref` ya redujo a key). Una URL de otro origen conserva
    `://` tras normalizar (FR-005)."""
    return "://" not in value


def decide_image_change(
    *,
    tenant_schema: str,
    folder: str,
    sent: str | None,
    base_provided: bool,
    base: str | None,
    current: str | None,
    is_creation: bool,
) -> ImageDecision:
    """Decide qué hacer con la imagen que envía un formulario (research D4).

    Todos los valores entran ya normalizados con `normalize_asset_ref`. Orden
    fijo: (1) vacío -> KEEP; (2) igual a la vigente -> KEEP (no es una key nueva,
    aunque sea histórica y fuera de convención); (3) gestionada con forma
    inválida -> `AssetKeyError` siempre, con o sin base; (4) creación -> APPLY;
    (5) sin base o base distinta de la vigente -> IGNORE; (6) otro origen ->
    APPLY sin verificar; (7) gestionada -> APPLY (el llamador verifica que exista).
    """
    if sent is None:
        return ImageDecision.KEEP
    if sent == current:
        return ImageDecision.KEEP
    managed = is_managed_ref(sent)
    if managed:
        validate_asset_key(sent, tenant_schema, folder)  # lanza AssetKeyError
    if is_creation:
        return ImageDecision.APPLY
    if not base_provided or base != current:
        return ImageDecision.IGNORE
    return ImageDecision.APPLY


def ensure_image_exists(key: str) -> None:
    """Verifica que el archivo exista en R2 antes de guardar su key (FR-003).
    422 si no existe; 503 (falla cerrado) si el almacenamiento no responde."""
    try:
        exists = object_exists(key)
    except StorageUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_STORAGE_UNAVAILABLE)
    if not exists:
        logger.info("Imagen inexistente en R2: '%s'", key)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MSG_IMAGE_NOT_FOUND)


def image_key_error_to_http(exc: AssetKeyError, sent: str) -> HTTPException:
    """422 con mensaje genérico; el detalle de la key va al log, no a la respuesta."""
    logger.warning("Key de imagen rechazada: %r (%s)", sent, exc)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MSG_IMAGE_INVALID)


# ---------------------------------------------------------------------------
# ¿Otra fila usa este archivo? (FR-006, research D9) y candado por key (D13)
# ---------------------------------------------------------------------------

def _reference_candidates(key: str) -> list[str]:
    """Valores con los que una fila puede referenciar `key`: la key misma y su URL
    absoluta con cada prefijo gestionado (filas históricas sin migrar)."""
    return [key] + [f"{prefix}{key}" for prefix in _managed_bucket_prefixes()]


def is_key_referenced(db: Session, key: str) -> bool:
    """`True` si alguna de las cuatro fuentes referencia `key` (o su URL absoluta
    gestionada equivalente): `products.image_url`, valores de
    `payment_methods.payment_info`, `order_payment_attempts.receipt_file_url`
    (esquema del negocio de `db`) y `shared.tenants.logo_url` (global, de todos los
    negocios). Solo lectura."""
    candidates = _reference_candidates(key)

    if db.execute(
        select(Product.id).where(Product.image_url.in_(candidates)).limit(1)
    ).first() is not None:
        return True

    # `payment_info` es JSONB: se recorre en Python (decenas de filas por negocio) para no
    # depender de `jsonb_each_text`, que SQLite de los tests no tiene.
    for (info,) in db.execute(select(PaymentMethod.payment_info)).all():
        for value in (info or {}).values():
            if isinstance(value, str) and normalize_asset_ref(value) == key:
                return True

    if db.execute(
        select(OrderPaymentAttempt.id)
        .where(OrderPaymentAttempt.receipt_file_url.in_(candidates))
        .limit(1)
    ).first() is not None:
        return True

    # Sin filtrar por negocio: cubre el logo de cualquier negocio que use este prefijo.
    return db.execute(
        select(Tenant.id).where(Tenant.logo_url.in_(candidates)).limit(1)
    ).first() is not None


def asset_key_lock(db: Session, key: str) -> None:
    """Candado consultivo de PostgreSQL por key, liberado solo al terminar la
    transacción (research D13). Serializa "verificar que existe y guardar" contra
    "comprobar que nadie lo usa y borrar" sobre la misma key. No-op fuera de
    PostgreSQL (los tests usan SQLite)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


def resolve_image_change(
    db: Session,
    *,
    tenant_schema: str,
    folder: str,
    sent: str | None,
    base_provided: bool,
    base: str | None,
    current: str | None,
    is_creation: bool,
) -> ImageDecision:
    """Resuelve una imagen enviada por un formulario (FR-001…FR-005) **antes** de
    que el llamador modifique el registro: un 422/503 deja todo intacto (research D6).

    `decide_image_change` + conversión de `AssetKeyError` a 422 + (si se va a aplicar
    una key gestionada) candado por key y verificación de existencia en R2. Los
    valores entran ya normalizados con `normalize_asset_ref`.
    """
    try:
        decision = decide_image_change(
            tenant_schema=tenant_schema, folder=folder, sent=sent,
            base_provided=base_provided, base=base, current=current,
            is_creation=is_creation,
        )
    except AssetKeyError as exc:
        raise image_key_error_to_http(exc, sent or "")
    if decision is ImageDecision.APPLY and sent is not None and is_managed_ref(sent):
        # El candado se conserva hasta el commit de la petición: cubre "verificar que
        # existe" + "escribir la referencia" frente a un borrado concurrente (D13).
        asset_key_lock(db, sent)
        try:
            ensure_image_exists(sent)
        except HTTPException:
            db.rollback()  # libera el candado; aún no se ha modificado nada
            raise
    return decision


def delete_if_unreferenced(db: Session, key: str, delete_fn) -> bool:
    """Borra `key` con `delete_fn` solo si ninguna de las cuatro fuentes la usa
    (FR-006), tomando el candado por key para que un guardado concurrente que
    acaba de verificar la misma key no quede con una referencia rota (FR-007, D13).

    Mejor esfuerzo (A-44 intacto): se llama **después** del commit del cambio y
    cualquier fallo se registra en el log sin propagarse; a lo sumo queda un
    huérfano. Termina la transacción corta (rollback) para liberar el candado.
    Devuelve `True` si borró.
    """
    try:
        asset_key_lock(db, key)
        if is_key_referenced(db, key):
            return False
        delete_fn(key)
        return True
    except Exception:
        logger.exception("No se pudo evaluar/borrar el archivo reemplazado '%s'", key)
        return False
    finally:
        db.rollback()


# ---------------------------------------------------------------------------
# Comprobantes del comensal (FR-008)
# ---------------------------------------------------------------------------

def resolve_receipt_key(value: str, tenant_schema: str) -> str:
    """Key lista para persistir de un comprobante nuevo, o `HTTPException`.

    Acepta una key o una URL del bucket gestionado (dominio anterior o de assets).
    Solo se garantiza prefijo del negocio + carpeta `comprobantes` + existencia:
    el comensal no tiene sesión, así que no se garantiza quién subió el archivo.
    """
    ref = normalize_asset_ref(value)
    if ref is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MSG_RECEIPT_INVALID)
    if not is_managed_ref(ref):
        logger.warning("Comprobante de otro origen rechazado: %r", value)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MSG_RECEIPT_FOREIGN)
    try:
        validate_asset_key(ref, tenant_schema, FOLDER_RECEIPTS)
    except AssetKeyError as exc:
        logger.warning("Comprobante rechazado: %r (%s)", value, exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, MSG_RECEIPT_INVALID)
    ensure_image_exists(ref)
    return ref
