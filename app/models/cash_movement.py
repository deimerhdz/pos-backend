from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Numeric, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from decimal import Decimal
from datetime import datetime


class CashMovement(UUIDPrimaryKeyMixin, Base):
    """Entrada/salida de efectivo del turno distinta a una venta (retiros,
    fondos, gastos menores)."""

    __tablename__ = "cash_movements"

    cash_shift_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type: Mapped[str] = mapped_column(String(10), nullable=False)

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    description: Mapped[str] = mapped_column(String(255), nullable=False)

    # Referencia blanda a shared.users.id (sin FK cross-schema).
    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("type IN ('in', 'out')", name="ck_cash_movement_type"),
        CheckConstraint("amount > 0", name="ck_cash_movement_amount_positive"),
        {"schema": "tenant"},
    )
