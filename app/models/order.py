
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, Integer, String, Numeric, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING, Optional, List
from decimal import Decimal

if TYPE_CHECKING:
    from .product import Product
    from .variant import Variant
    from .order_item_modifier import OrderItemModifier


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tables.id"), nullable=False, index=True
    )

    # null = orden de toda la mesa; con valor = orden individual de un comensal
    table_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=True, index=True
    )

    scope: Mapped[str] = mapped_column(String(20), nullable=False)

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    # subtotal = pre-impuesto; total = gran total (subtotal + impuestos exclusivos)
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    items: Mapped[List["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("scope IN ('individual', 'table')", name="ck_order_scope"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_order_status",
        ),
        {"schema": "tenant"},
    )


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )

    variant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("variants.id"), nullable=True
    )

    product_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    # quién pidió el item (útil en órdenes de mesa)
    table_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=True
    )

    product_name: Mapped[str] = mapped_column(String(255), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    order: Mapped["Order"] = relationship("Order", back_populates="items")

    variant: Mapped[Optional["Variant"]] = relationship("Variant")

    product: Mapped[Optional["Product"]] = relationship("Product")

    modifiers: Mapped[List["OrderItemModifier"]] = relationship(
        "OrderItemModifier", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
