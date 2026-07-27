from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Numeric, ForeignKey, DateTime, func
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from decimal import Decimal
from datetime import datetime


class CashPartialCount(UUIDPrimaryKeyMixin, Base):
    """Arqueo parcial durante el turno (RF-046): conteo intermedio que NO cierra
    la caja. Snapshotea esperado/contado/diferencia al momento del conteo."""

    __tablename__ = "cash_partial_counts"

    cash_shift_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    counted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    difference: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Referencia blanda a shared.users.id + snapshot.
    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    counted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = ({"schema": "tenant"},)
