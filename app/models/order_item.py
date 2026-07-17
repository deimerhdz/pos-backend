from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .customer_order import CustomerOrder


class OrderItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("customer_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped["CustomerOrder"] = relationship(back_populates="items")

    # Origen del comensal (para el split de cuenta por sesión en Fase 7).
    # Nullable: las órdenes de mostrador no tienen sesión de mesa.
    session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_sessions.id"), nullable=True, index=True
    )

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id"), nullable=False, index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    # Ciclo de cocina (KDS), independiente del status de pago de la orden.
    # La fuente de verdad de las transiciones pendiente→...→entregado es el KDS
    # (Fase 6). 'anulado' se excluye de la validación de bloqueo de cobro.
    estado_cocina: Mapped[str] = mapped_column(
        String(15), nullable=False, server_default="pendiente"
    )

    # Si este ítem reemplaza a uno anulado (void + recreación, Fase 6).
    void_de: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("order_items.id"), nullable=True
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    options: Mapped[List["OrderItemOption"]] = relationship(
        back_populates="order_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_item_quantity_positive"),
        CheckConstraint(
            "estado_cocina IN ('pendiente', 'en_preparacion', 'listo', 'entregado', 'anulado')",
            name="ck_order_item_estado_cocina",
        ),
        {"schema": "tenant"},
    )


class OrderItemOption(UUIDPrimaryKeyMixin, Base):
    """Opción elegida en una línea de comanda (p.ej. un sabor)."""

    __tablename__ = "order_item_options"

    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_item: Mapped["OrderItem"] = relationship(back_populates="options")

    option_id: Mapped[UUID] = mapped_column(
        ForeignKey("options.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "order_item_id", "option_id",
            name="uq__order_item_options__order_item_id__option_id",
        ),
        {"schema": "tenant"},
    )
