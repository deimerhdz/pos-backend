
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING, Optional, List

if TYPE_CHECKING:
    from .product import Product
    from .variant import Variant
    from .table_session import TableSession
    from .cart_item_modifier import CartItemModifier


class CartItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cart_items"

    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tables.id"), nullable=False, index=True
    )

    table_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )

    # Cutover: la venta es por variante. product_id queda para continuidad/reportes.
    variant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("variants.id"), nullable=True, index=True
    )

    product_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("products.id"), nullable=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    variant: Mapped[Optional["Variant"]] = relationship("Variant")

    product: Mapped[Optional["Product"]] = relationship("Product")

    table_session: Mapped[Optional["TableSession"]] = relationship("TableSession")

    modifiers: Mapped[List["CartItemModifier"]] = relationship(
        "CartItemModifier", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
