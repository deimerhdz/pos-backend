from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .cart import Cart


class CartItem(UUIDPrimaryKeyMixin, Base):
    """Línea del carrito. Espeja a `order_items` (variante + snapshot de precio +
    opciones); al consolidar se copia tal cual a una `order_item`."""

    __tablename__ = "cart_items"

    cart_id: Mapped[UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cart: Mapped["Cart"] = relationship(back_populates="items")

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id"), nullable=False, index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    options: Mapped[List["CartItemOption"]] = relationship(
        back_populates="cart_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_item_quantity_positive"),
        {"schema": "tenant"},
    )


class CartItemOption(UUIDPrimaryKeyMixin, Base):
    """Opción elegida en una línea de carrito (p.ej. un sabor). Espejo de
    `order_item_options`."""

    __tablename__ = "cart_item_options"

    cart_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("cart_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cart_item: Mapped["CartItem"] = relationship(back_populates="options")

    option_id: Mapped[UUID] = mapped_column(
        ForeignKey("options.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "cart_item_id", "option_id",
            name="uq__cart_item_options__cart_item_id__option_id",
        ),
        {"schema": "tenant"},
    )
