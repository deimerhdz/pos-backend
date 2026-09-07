from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, UUIDPrimaryKeyMixin


class PushSubscription(UUIDPrimaryKeyMixin, Base):
    """Suscripción push de un dispositivo/navegador de un usuario del staff
    (spec 077, RF-004). `user_id` es una referencia blanda a `shared.users.id`,
    mismo patrón que `AuditLog.user_id`."""

    __tablename__ = "push_subscriptions"

    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    endpoint: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    p256dh_key: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_key: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Se marca `false` cuando el push service responde 404/410 (suscripción
    # caducada/revocada). Nunca se borra en caliente dentro de una request de
    # negocio (data-model.md § PushSubscription).
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = ({"schema": "tenant"},)
