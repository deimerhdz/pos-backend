
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .attribute import Attribute


class AttributeValue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attribute_values"

    attribute_id: Mapped[UUID] = mapped_column(
        ForeignKey("attributes.id"), nullable=False, index=True
    )

    value: Mapped[str] = mapped_column(String(150), nullable=False)

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    attribute: Mapped[Optional["Attribute"]] = relationship(back_populates="values")

    __table_args__ = (
        UniqueConstraint("attribute_id", "value"),
        {"schema": "tenant"},
    )
