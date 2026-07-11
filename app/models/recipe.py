
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Boolean, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .recipe_item import RecipeItem


class Recipe(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Receta (BOM) de una unidad vendible: dueño = variante XOR modificador."""

    __tablename__ = "recipes"

    variant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("variants.id"), nullable=True, index=True
    )

    modifier_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("modifiers.id"), nullable=True, index=True
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Reventa directa: receta 1:1 (quantity=1, unidad = unidad base del insumo).
    is_resale: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    items: Mapped[List["RecipeItem"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "(variant_id IS NOT NULL)::int + (modifier_id IS NOT NULL)::int = 1",
            name="ck_recipe_single_owner",
        ),
        UniqueConstraint("variant_id", name="uq__recipes__variant_id"),
        UniqueConstraint("modifier_id", name="uq__recipes__modifier_id"),
        {"schema": "tenant"},
    )
