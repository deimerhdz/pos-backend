
from datetime import datetime
from decimal import Decimal

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Numeric, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional


class StockReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Reserva de insumo creada al pedir (QR) y consumida al cobrar (caja).

    Reduce la disponibilidad visible sin tocar los lotes: el consumo real ocurre
    en el cobro (`consume_from_batches`). Cancelar/expirar la libera sin movimiento.
    """

    __tablename__ = "stock_reservations"

    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)

    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id"), nullable=False, index=True
    )

    supply_id: Mapped[UUID] = mapped_column(ForeignKey("supplies.id"), nullable=False, index=True)

    quantity_reserved: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="ck_reservation_status",
        ),
        {"schema": "tenant"},
    )
