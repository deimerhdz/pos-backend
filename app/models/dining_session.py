from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func, CheckConstraint, Index, text
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from datetime import datetime


class DiningSession(UUIDPrimaryKeyMixin, Base):
    """Sesión de consumo en una mesa. `customer_name` es el nombre que el
    cliente escribe al escanear el QR (cliente anónimo)."""

    __tablename__ = "dining_sessions"

    dining_table_id: Mapped[UUID] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=False, index=True
    )

    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="open")

    opened_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_dining_session_status"),
        # Sólo una sesión abierta por mesa a la vez.
        Index(
            "idx_open_session_per_table",
            "dining_table_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        {"schema": "tenant"},
    )
