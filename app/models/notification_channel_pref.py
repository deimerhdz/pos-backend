from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, UUIDPrimaryKeyMixin


class NotificationChannelPreference(UUIDPrimaryKeyMixin, Base):
    """Qué canales están habilitados por tipo de evento, por tenant (spec 077,
    RF-010). La **ausencia** de fila para un `(event_type, channel)` se
    interpreta como habilitado (data-model.md § Regla de lectura) — ningún
    tenant existente necesita una fila sembrada por migración."""

    __tablename__ = "notification_channel_preferences"

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        UniqueConstraint("event_type", "channel"),
        {"schema": "tenant"},
    )
