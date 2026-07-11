
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Numeric, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .recipe import Recipe
    from .supply import Supply
    from .unit_measure import UnitMeasure


class RecipeItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un insumo consumido por la receta, con su cantidad y unidad."""

    __tablename__ = "recipe_items"

    recipe_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipes.id"), nullable=False, index=True
    )

    supply_id: Mapped[UUID] = mapped_column(ForeignKey("supplies.id"), nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)

    unit_measure_id: Mapped[UUID] = mapped_column(
        ForeignKey("unit_measures.id"), nullable=False
    )

    recipe: Mapped[Optional["Recipe"]] = relationship(back_populates="items")

    supply: Mapped[Optional["Supply"]] = relationship()

    unit_measure: Mapped[Optional["UnitMeasure"]] = relationship()

    @property
    def supply_name(self) -> Optional[str]:
        return self.supply.name if self.supply else None

    @property
    def unit(self) -> Optional[str]:
        return self.unit_measure.abbreviation if self.unit_measure else None

    __table_args__ = ({"schema": "tenant"},)
