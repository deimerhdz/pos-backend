from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .option_group import OptionGroup


class Option(UUIDPrimaryKeyMixin, Base):
    """Valor dentro de un grupo. `inventory_item_id` liga la opción al stock:
    elegirla descuenta `item_quantity` del insumo al venderse."""

    __tablename__ = "options"

    option_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("option_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    option_group: Mapped["OptionGroup"] = relationship(back_populates="options")

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    extra_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )

    inventory_item_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("inventory_items.id"), nullable=True
    )

    item_quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=0, server_default="0"
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        CheckConstraint("extra_price >= 0", name="ck_option_extra_price_positive"),
        UniqueConstraint("option_group_id", "name", name="uq__options__option_group_id__name"),
        {"schema": "tenant"},
    )
