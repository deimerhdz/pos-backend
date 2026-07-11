
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .modifier import Modifier


class CartItemModifier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cart_item_modifiers"

    cart_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("cart_items.id"), nullable=False, index=True
    )

    modifier_id: Mapped[UUID] = mapped_column(ForeignKey("modifiers.id"), nullable=False)

    modifier: Mapped[Optional["Modifier"]] = relationship("Modifier")

    __table_args__ = ({"schema": "tenant"},)
