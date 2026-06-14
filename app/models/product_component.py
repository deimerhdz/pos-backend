
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey,Numeric,UniqueConstraint
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from typing import TYPE_CHECKING,Optional
from decimal import Decimal

if TYPE_CHECKING:
    from .product import Product


class ProductComponent(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "product_component"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False)

    component_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)

    product: Mapped[Optional["Product"]] = relationship(
        "Product", foreign_keys=[product_id], back_populates="components"
    )

    component: Mapped[Optional["Product"]] = relationship(
        "Product", foreign_keys=[component_id]
    )

    @property
    def name(self) -> Optional[str]:
        return self.component.name if self.component else None

    __table_args__ = (
        UniqueConstraint("product_id", "component_id"),
        {"schema": "tenant"},
    )
