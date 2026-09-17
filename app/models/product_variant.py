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
    from .presentation import Presentation


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Línea vendible: aquí viven el precio y la receta. Productos sin tamaños
    obtienen una variante 'Presentación única' (spec 083, A-74)."""

    __tablename__ = "product_variants"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product: Mapped["Product"] = relationship(back_populates="variants")

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="Presentación única"
    )

    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Posición de despliegue dentro del producto (spec 042): determina el orden en el
    # formulario y en el detalle del Menú QR. Sin default de ORM -- toda ruta que crea
    # una variante (fixtures de test incluidas) debe asignarlo explícitamente.
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)

    # spec 063 (A-63) eliminó `presentation_id` (entidad `Presentation` de spec 040
    # revertida) porque las promociones referencian `product_variants` directamente
    # vía `promotion_variants` -- eso no cambia. spec 084 reintroduce la columna con
    # un propósito distinto: asociar la variante con una `Presentation` del catálogo
    # nuevo de spec 083 (mero catálogo de nombres, sin rol en el alcance de una
    # promoción) para que su `name` quede sincronizado con esa presentación
    # (FR-002/FR-003/FR-004). Nullable y no retroactivo: toda variante existente
    # nace sin asociación (FR-007).
    presentation_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("presentations.id", ondelete="SET NULL"), nullable=True
    )
    presentation: Mapped[Optional["Presentation"]] = relationship()

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
        UniqueConstraint(
            "product_id", "presentation_id",
            name="uq__product_variants__product_id__presentation_id",
        ),
        {"schema": "tenant"},
    )
