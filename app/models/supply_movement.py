
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Numeric, ForeignKey, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from decimal import Decimal


class SupplyMovement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Kardex del insumo (fraccionable). Independiente de inventory_movements legacy."""

    __tablename__ = "supply_movements"

    supply_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplies.id"), nullable=False, index=True
    )

    batch_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("supply_batches.id"), nullable=True, index=True
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    type: Mapped[str] = mapped_column(String(20), nullable=False)

    reference_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('income', 'expense', 'adjust', 'waste')",
            name="ck_supply_movement_type",
        ),
        {"schema": "tenant"},
    )
