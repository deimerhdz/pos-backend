
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .modifier_group import ModifierGroup


class Modifier(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "modifiers"

    group_id: Mapped[UUID] = mapped_column(
        ForeignKey("modifier_groups.id"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    group: Mapped[Optional["ModifierGroup"]] = relationship(back_populates="modifiers")

    __table_args__ = ({"schema": "tenant"},)
