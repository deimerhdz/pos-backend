from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .product import Product
    from .recipe_item import RecipeItem
    from .variant_option_group import VariantOptionGroup


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

    # Posición de despliegue dentro del producto (spec 042): determina el orden en el
    # formulario y en el detalle del Menú QR. Sin default de ORM -- toda ruta que crea
    # una variante (fixtures de test incluidas) debe asignarlo explícitamente.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    # spec 063 (A-63): `presentation_id` se elimina — la entidad `Presentation` y
    # su modelo de datos (spec 040) se revierten. Las promociones referencian
    # `product_variants` directamente vía `promotion_variants`.

    recipe_items: Mapped[List["RecipeItem"]] = relationship(
        back_populates="product_variant", cascade="all, delete-orphan"
    )

    option_groups: Mapped[List["VariantOptionGroup"]] = relationship(
        back_populates="product_variant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_product_variant_price_positive"),
        UniqueConstraint("product_id", "name", name="uq__product_variants__product_id__name"),
        UniqueConstraint(
            "product_id", "display_order", name="uq__product_variants__product_id__display_order"
        ),
        {"schema": "tenant"},
    )
