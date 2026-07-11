
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, Date, ForeignKey, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from datetime import date
from decimal import Decimal

if TYPE_CHECKING:
    from .supply import Supply


class SupplyBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Lote de un insumo, con vencimiento. El consumo es FEFO sobre estos lotes."""

    __tablename__ = "supply_batches"

    supply_id: Mapped[UUID] = mapped_column(
        ForeignKey("supplies.id"), nullable=False, index=True
    )

    code: Mapped[str] = mapped_column(String(100), nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    expires_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=0, server_default="0"
    )

    received_at: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.now()
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    supply: Mapped[Optional["Supply"]] = relationship(back_populates="batches")

    __table_args__ = ({"schema": "tenant"},)
