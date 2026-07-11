
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.orm import mapped_column, Mapped, relationship
from sqlalchemy import String, Boolean
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .attribute_value import AttributeValue


class Attribute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "attributes"

    name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)

    affects_inventory: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    values: Mapped[List["AttributeValue"]] = relationship(
        back_populates="attribute", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
