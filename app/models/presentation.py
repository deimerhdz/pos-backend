from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy import Boolean, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column


class Presentation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Concepto de catálogo compartido del tenant (p. ej. "8oz", "16oz") al que
    variantes de productos distintos pueden apuntar (spec 040).

    **No es** la variante de producto ni su `name` libre: una variante conserva su
    `name` (puede decir "8oz") y **además** referencia, opcionalmente, una fila de
    `presentations`. Es la unidad natural de precio del negocio y el alcance de una
    regla de promoción `qty_price_presentation` se resuelve SIEMPRE por esta
    referencia, nunca comparando `ProductVariant.name` (research.md D1, FR-007).
    """

    __tablename__ = "presentations"

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Baja lógica. Una presentación inactiva no se ofrece en el selector de
    # variante ni se puede elegir en una regla nueva. La baja está BLOQUEADA
    # mientras una regla de una promoción `active` la referencie (FR-020, CL-2 —
    # ver `presentations/service.py`).
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq__presentations__name"),
        {"schema": "tenant"},
    )
