from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .product_variant import ProductVariant


class RecipeItem(UUIDPrimaryKeyMixin, Base):
    """BOM: insumos que una variante consume al venderse. `quantity` está en la
    unidad de medida del propio insumo (sin conversión)."""

    __tablename__ = "recipe_items"

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant: Mapped["ProductVariant"] = relationship(back_populates="recipe_items")

    inventory_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=False, index=True
    )

    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_recipe_item_qty_positive"),
        UniqueConstraint(
            "product_variant_id", "inventory_item_id",
            name="uq__recipe_items__product_variant_id__inventory_item_id",
        ),
        {"schema": "tenant"},
    )
