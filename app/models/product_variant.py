from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .product import Product
    from .recipe_item import RecipeItem


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Línea vendible: aquí viven el precio y la receta. Productos sin tamaños
    obtienen una variante 'Single'."""

    __tablename__ = "product_variants"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product: Mapped["Product"] = relationship(back_populates="variants")

    name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="Single")

    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    recipe_items: Mapped[List["RecipeItem"]] = relationship(
        back_populates="product_variant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_product_variant_price_positive"),
        UniqueConstraint("product_id", "name", name="uq__product_variants__product_id__name"),
        {"schema": "tenant"},
    )
