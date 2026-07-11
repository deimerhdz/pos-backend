
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .unit_measure import UnitMeasure
    from .supply_batch import SupplyBatch


class Supply(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Insumo a granel: sujeto real del inventario, medido en su unidad base."""

    __tablename__ = "supplies"

    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)

    unit_measure_id: Mapped[UUID] = mapped_column(
        ForeignKey("unit_measures.id"), nullable=False
    )

    stock_current: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, default=0, server_default="0"
    )

    stock_min: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False, default=0, server_default="0"
    )

    track_expiry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    unit_measure: Mapped[Optional["UnitMeasure"]] = relationship()

    batches: Mapped[List["SupplyBatch"]] = relationship(
        back_populates="supply", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
