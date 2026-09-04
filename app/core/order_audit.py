"""Auditoría del ciclo de vida de una orden (spec 074).

Emite un evento estructurado hacia Sentry Logs por cada transición del ciclo
de vida de una orden (creación, confirmación, intento de pago, confirmación
de pago, aprobación/rechazo de comprobante de transferencia, cancelación).
Por decisión de alcance del spec, Sentry es el único destino: este módulo no
persiste nada en base de datos ni introduce una tabla propia — ver
`specs/074-auditoria-ordenes/data-model.md` y
`specs/074-auditoria-ordenes/contracts/order-audit-log-event.md` en el
repositorio `pos-specs`.

El payload que sale hacia Sentry es plano (nunca un objeto/dict anidado como
valor de un atributo): Sentry Logs solo preserva como atributo filtrable un
valor `bool`/`int`/`float`/`str` — cualquier objeto anidado se degrada a un
`repr()` ilegible y no buscable (verificado contra el código real de
`sentry-sdk`, ver `specs/074-auditoria-ordenes/research.md` § 1).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import sentry_sdk

from app.core.config import settings

logger = logging.getLogger(__name__)


class OrderAuditEventType(str, Enum):
    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    PAYMENT_ATTEMPT_CREATED = "order.payment_attempt.created"
    PAYMENT_CASH_CONFIRMED = "order.payment.cash_confirmed"
    PAYMENT_TRANSFER_APPROVED = "order.payment.transfer_approved"
    PAYMENT_TRANSFER_REJECTED = "order.payment.transfer_rejected"
    ORDER_CANCELLED = "order.cancelled"
    PAYMENT_CHECKOUT_AND_SEND = "order.payment.checkout_and_send"


class ActorType(str, Enum):
    COMENSAL = "comensal"
    CAJERO = "cajero"
    SISTEMA = "sistema"


@dataclass(frozen=True)
class OrderAuditActor:
    """Quién origina un evento de auditoría — nunca una FK a `users` (FR-003).

    `id` es `participant_id` para un comensal, `user_id` para un cajero, y
    `None` para el sistema. `role` solo aplica a `cajero`.
    """

    type: ActorType
    id: Optional[str] = None
    role: Optional[str] = None


def _hash_sensitive(value: str) -> str:
    """HMAC-SHA256 de `value` con `AUDIT_HASH_SECRET` — mismo valor, mismo hash siempre.

    Clave dedicada, nunca `JWT_SECRET` ni ningún otro secreto existente (research.md § 3):
    rotar uno no debe afectar al otro. Si `AUDIT_HASH_SECRET` no está configurado,
    falla explícito en vez de caer en un secreto de otro dominio.
    """
    if not settings.AUDIT_HASH_SECRET:
        raise RuntimeError(
            "AUDIT_HASH_SECRET no está configurado — no se puede transformar un dato "
            "sensible para el log de auditoría de órdenes (spec 074)."
        )
    return hmac.new(
        settings.AUDIT_HASH_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def hash_sensitive_or_none(value: Optional[str]) -> Optional[str]:
    """Variante tolerante de `_hash_sensitive`, pensada para los puntos de
    integración: devuelve `None` si no hay valor que transformar o si
    `AUDIT_HASH_SECRET` no está configurado, en vez de propagar el error.

    La auditoría nunca puede romper la transición de negocio que audita
    (FR-011), y este es el único punto de los llamadores donde se ejecuta
    lógica antes de entrar al `try` de `record_order_audit_event`. Un `None`
    se omite del payload (ver más abajo): ante un secreto ausente el evento
    viaja sin el campo — jamás con el valor sensible en texto plano (FR-005).
    """
    if not value:
        return None
    try:
        return _hash_sensitive(value)
    except Exception:
        logger.exception(
            "No se pudo transformar un dato sensible para el log de auditoría "
            "de órdenes (spec 074) — el evento se emite sin ese campo"
        )
        return None


def record_order_audit_event(
    *,
    event_type: OrderAuditEventType,
    order_id,
    tenant_id: int,
    actor: OrderAuditActor,
    details: Optional[dict] = None,
) -> None:
    """Emite un evento de auditoría de orden hacia Sentry Logs.

    Aplana `actor`/`details` en atributos de nivel superior (nunca un objeto
    anidado) y omite cualquier valor `None` en vez de enviarlo. Nunca bloquea
    ni revierte la transición de negocio que audita (FR-011): cualquier falla
    al construir o enviar el evento se captura aquí, nunca se propaga hacia
    quien llama.
    """
    try:
        if settings.ENVIRONMENT != "prod":
            return

        attributes: dict = {
            "event_type": event_type.value,
            "order_id": str(order_id),
            "tenant_id": tenant_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "actor_type": actor.type.value,
        }
        if actor.id is not None:
            attributes["actor_id"] = str(actor.id)
        if actor.role is not None:
            attributes["actor_role"] = actor.role

        for key, value in (details or {}).items():
            if value is not None:
                attributes[key] = value

        sentry_sdk.logger.info(event_type.value, attributes=attributes)
    except Exception:
        logger.exception(
            "No se pudo registrar el evento de auditoría de orden %s (order_id=%s)",
            event_type,
            order_id,
        )
