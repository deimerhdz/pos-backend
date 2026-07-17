from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from datetime import datetime


class DiningSession(UUIDPrimaryKeyMixin, Base):
    """Sesión de consumo **por comensal** (varias por mesa). `customer_name` es
    el nombre que el cliente escribe al escanear el QR (cliente anónimo).

    `expires_at` implementa el TTL de sesión (Fase 0: 4h). Fase 3 lo puebla al
    abrir y lo desliza en cada actividad del carrito; si expira, la sesión pasa
    a 'closed' y su carrito queda abandonado (sin impacto de inventario)."""

    __tablename__ = "dining_sessions"

    dining_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )

    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")

    opened_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # TTL de la sesión del comensal (nullable: lo puebla/desliza Fase 3).
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_dining_session_status"),
        {"schema": "tenant"},
    )
