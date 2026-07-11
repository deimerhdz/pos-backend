
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Numeric, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from decimal import Decimal


class OrderItemModifier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Modificador elegido en una línea de orden (snapshot de nombre y precio)."""

    __tablename__ = "order_item_modifiers"

    order_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("order_items.id"), nullable=False, index=True
    )

    modifier_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("modifiers.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    __table_args__ = ({"schema": "tenant"},)
