
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .variant import Variant
    from .attribute_value import AttributeValue


class VariantValue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "variant_values"

    variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("variants.id"), nullable=False, index=True
    )

    attribute_value_id: Mapped[UUID] = mapped_column(
        ForeignKey("attribute_values.id"), nullable=False
    )

    variant: Mapped[Optional["Variant"]] = relationship(back_populates="values")

    attribute_value: Mapped[Optional["AttributeValue"]] = relationship()

    @property
    def value(self) -> Optional[str]:
        return self.attribute_value.value if self.attribute_value else None

    __table_args__ = (
        UniqueConstraint("variant_id", "attribute_value_id"),
        {"schema": "tenant"},
    )
