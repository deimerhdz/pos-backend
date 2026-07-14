from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Integer, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .product import Product
    from .option_group import OptionGroup


class ProductOptionGroup(UUIDPrimaryKeyMixin, Base):
    """Qué grupos de opciones aplican a cada producto (p.ej. 'tres sabores a
    elegir'). min/max_select pueden sobreescribir los del grupo por producto."""

    __tablename__ = "product_option_groups"

    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product: Mapped["Product"] = relationship(back_populates="option_groups")

    option_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("option_groups.id"), nullable=False, index=True
    )
    option_group: Mapped["OptionGroup"] = relationship()

    min_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    max_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint("max_select >= min_select", name="ck_product_option_group_max_ge_min"),
        UniqueConstraint(
            "product_id", "option_group_id",
            name="uq__product_option_groups__product_id__option_group_id",
        ),
        {"schema": "tenant"},
    )
