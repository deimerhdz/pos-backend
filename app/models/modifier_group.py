
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy import String, Boolean, Integer
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .modifier import Modifier


class ModifierGroup(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "modifier_groups"

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    min_select: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    max_select: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    modifiers: Mapped[List["Modifier"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
