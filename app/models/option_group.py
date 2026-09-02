from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy import String, Integer, Boolean, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .option import Option


class OptionGroup(UUIDPrimaryKeyMixin, Base):
    """Grupo de opciones (sabores de helado, sabor de limonada, mezcla de
    michelada...). min/max_select gobiernan cuántas se pueden elegir."""

    __tablename__ = "option_groups"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    min_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    max_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # spec 064: "incluido" bloquea cualquier precio distinto de $0 en sus opciones (un
    # sabor ya cubierto por el precio de la presentación); "con_recargo" permite precio
    # libre por opción (un topping). Sin default de negocio a nivel de ORM -- el
    # `server_default` solo protege un INSERT que no lo mencione; el schema `OptionGroupCreate`
    # exige elegirlo explícitamente (research.md Decisión 1).
    pricing_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="con_recargo"
    )

    # spec 065: "conteo" (default) es el comportamiento actual -- min_select/max_select
    # cuentan opciones distintas. "cantidad" deja que el cliente elija unidades libres por
    # opción, sin mínimo posible; min_select/max_select se ignoran en ese modo.
    selection_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="conteo"
    )

    max_quantity_per_option: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    max_total_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    options: Mapped[List["Option"]] = relationship(
        back_populates="option_group", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("min_select >= 0", name="ck_option_group_min_select"),
        CheckConstraint("max_select >= min_select", name="ck_option_group_max_ge_min"),
        CheckConstraint(
            "pricing_type IN ('incluido', 'con_recargo')",
            name="ck_option_group_pricing_type",
        ),
        CheckConstraint(
            "selection_mode IN ('conteo', 'cantidad')",
            name="ck_option_group_selection_mode",
        ),
        CheckConstraint(
            "max_quantity_per_option IS NULL OR max_quantity_per_option > 0",
            name="ck_option_group_max_quantity_per_option",
        ),
        CheckConstraint(
            "max_total_quantity IS NULL OR max_total_quantity > 0",
            name="ck_option_group_max_total_quantity",
        ),
        {"schema": "tenant"},
    )
