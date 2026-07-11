
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .variant_value import VariantValue


class Variant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "variants"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id"), nullable=False, index=True
    )

    sku: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    price: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0"
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    values: Mapped[List["VariantValue"]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": "tenant"},)
