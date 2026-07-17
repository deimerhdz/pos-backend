from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy import String, Integer, CheckConstraint
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

    options: Mapped[List["Option"]] = relationship(
        back_populates="option_group", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("min_select >= 0", name="ck_option_group_min_select"),
        CheckConstraint("max_select >= min_select", name="ck_option_group_max_ge_min"),
        {"schema": "tenant"},
    )
