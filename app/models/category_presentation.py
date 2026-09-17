from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .category import Category
    from .presentation import Presentation


class CategoryPresentation(UUIDPrimaryKeyMixin, Base):
    """Tabla puente: qué presentaciones del catálogo global están habilitadas para
    una categoría (spec 083, FR-004). Espejo de `VariantOptionGroup`
    (`app/models/variant_option_group.py`), sin columnas de negocio propias --
    una fila solo significa "pertenece", sin cardinalidad ni configuración."""

    __tablename__ = "category_presentations"

    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Sin ondelete: `Presentation` no tiene borrado físico (D4), así que nunca hay
    # un DELETE de la fila padre que deba propagarse.
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint(
            "category_id", "presentation_id",
            name="uq__category_presentations__category_id__presentation_id",
        ),
        {"schema": "tenant"},
    )
