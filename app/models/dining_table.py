import uuid
from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, Boolean, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional


class DiningTable(UUIDPrimaryKeyMixin, Base):
    """Mesa física. `qr_token` es el identificador público del QR pegado en la
    mesa; el cliente lo escanea y abre una sesión anónima."""

    __tablename__ = "dining_tables"

    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    qr_token: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )

    # Habilitación de la mesa (mesa dada de alta / de baja).
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Ocupación operativa (distinto de `active`). La liberación a 'libre' está
    # sujeta a la regla dura de Fase 7 (ninguna orden propia no-terminal).
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="libre"
    )

    __table_args__ = (
        CheckConstraint("status IN ('libre', 'ocupada')", name="ck_dining_table_status"),
        {"schema": "tenant"},
    )
