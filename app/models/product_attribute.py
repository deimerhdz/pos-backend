
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .attribute import Attribute


class ProductAttribute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_attributes"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )

    attribute_id: Mapped[UUID] = mapped_column(ForeignKey("attributes.id"), nullable=False)

    attribute: Mapped[Optional["Attribute"]] = relationship()

    __table_args__ = (
        UniqueConstraint("product_id", "attribute_id"),
        {"schema": "tenant"},
    )
