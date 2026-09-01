from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy import String, Integer, Boolean, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, TYPE_CHECKING

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
        {"schema": "tenant"},
    )
