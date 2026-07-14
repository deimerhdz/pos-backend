from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import (
    String, Integer, Numeric, ForeignKey, DateTime, func, CheckConstraint, text,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime

if TYPE_CHECKING:
    from .payment import Payment


class Sale(UUIDPrimaryKeyMixin, Base):
    """Venta emitida. Ligada al turno de caja para la conciliación. `user_id`/
    `user_name` = cajero (referencia blanda a shared.users + snapshot)."""

    __tablename__ = "sales"

    dining_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_sessions.id"), nullable=True
    )

    dining_table_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=True
    )

    cash_shift_id: Mapped[UUID] = mapped_column(
        ForeignKey("cash_shifts.id"), nullable=False, index=True
    )

    # Cajero: referencia blanda a shared.users.id (sin FK cross-schema) + snapshot.
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    tip: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="issued")

    sold_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    items: Mapped[List["SaleItem"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )

    payments: Mapped[List["Payment"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('issued', 'paid', 'void')", name="ck_sale_status"
        ),
        {"schema": "tenant"},
    )


class SaleItem(UUIDPrimaryKeyMixin, Base):
    """Línea de venta. `description`/`options` son copias inmutables al momento
    de la venta (historial)."""

    __tablename__ = "sale_items"

    sale_id: Mapped[UUID] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sale: Mapped["Sale"] = relationship(back_populates="items")

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id"), nullable=False
    )

    description: Mapped[str] = mapped_column(String(500), nullable=False)

    options: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_item_quantity_positive"),
        {"schema": "tenant"},
    )
