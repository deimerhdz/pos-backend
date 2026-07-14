from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped
from decimal import Decimal


class CashCountDenomination(UUIDPrimaryKeyMixin, Base):
    """Conteo de billetes/monedas al cierre del turno (arqueo por denominación)."""

    __tablename__ = "cash_count_denominations"

    cash_shift_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    denomination: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("denomination > 0", name="ck_cash_count_denomination_positive"),
        CheckConstraint("quantity >= 0", name="ck_cash_count_quantity_positive"),
        UniqueConstraint(
            "cash_shift_id", "denomination",
            name="uq__cash_count_denominations__cash_shift_id__denomination",
        ),
        {"schema": "tenant"},
    )
