
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .modifier_group import ModifierGroup


class ProductModifierGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_modifier_groups"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )

    group_id: Mapped[UUID] = mapped_column(ForeignKey("modifier_groups.id"), nullable=False)

    group: Mapped[Optional["ModifierGroup"]] = relationship()

    __table_args__ = (
        UniqueConstraint("product_id", "group_id"),
        {"schema": "tenant"},
    )
