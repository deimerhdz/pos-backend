
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .product import Product
    from .table_session import TableSession


class CartItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cart_items"

    table_id: Mapped[UUID] = mapped_column(
        ForeignKey("tables.id"), nullable=False, index=True
    )

    table_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=False, index=True
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped[Optional["Product"]] = relationship("Product")

    table_session: Mapped[Optional["TableSession"]] = relationship("TableSession")

    __table_args__ = (
        UniqueConstraint("table_session_id", "product_id"),
        {"schema": "tenant"},
    )
