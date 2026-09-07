from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, UUIDPrimaryKeyMixin


class NotificationEvent(UUIDPrimaryKeyMixin, Base):
    """Copia durable de un hecho de negocio dirigido al staff (spec 077,
    RF-006). No reemplaza al bus de tiempo real (`app/core/events.py`): es la
    fuente de verdad para el listado paginado, la recuperación tras
    desconexión (RF-008) y la purga por retención (RNF-005)."""

    __tablename__ = "notification_events"

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    related_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    related_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    # Retención vigente al crear la fila (`created_at` + `tenants.notification_
    # retention_days` de ese momento) — no se recalcula si el tenant cambia su
    # retención después (data-model.md § Nota de purga).
    purge_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    # `NULL` = pendiente para todo el tenant (marca compartida, no por usuario).
    attended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    attended_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    # Resumen informativo de intentos por canal, p. ej.
    # {"in_app": {"delivered_at": "..."}, "push": {"attempted": 3, "delivered": 2}}.
    delivery: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    __table_args__ = ({"schema": "tenant"},)
