from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import Integer, Numeric, ForeignKey, CheckConstraint, UniqueConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .product_variant import ProductVariant
    from .option_group import OptionGroup


class VariantOptionGroup(UUIDPrimaryKeyMixin, Base):
    """Qué grupo de opciones ofrece una variante, cuántas puede elegir el cliente y
    cuánto insumo descuenta cada elección.

    Vive en la **variante** y no en el producto porque las tres cosas cambian con el
    tamaño: la ensalada pequeña elige 1 sabor y descuenta 60 g, la mediana elige 2 y
    descuenta 120 g de cada uno. Con la configuración a nivel de producto eso obligaba
    a crear un producto por tamaño y a duplicar la lista de sabores en cada uno.

    Sustituye a `product_option_groups` (que tenía min/max por producto) y al antiguo
    "slot" de `recipe_items` (que tenía la cantidad por variante): tenerlas separadas
    era justo el problema. Una fila = «esta variante ofrece este grupo: elige N–M,
    descuenta X de cada uno».
    """

    __tablename__ = "variant_option_groups"

    product_variant_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant: Mapped["ProductVariant"] = relationship(back_populates="option_groups")

    # Sin ondelete: los grupos se retiran por soft-delete (`active=false`), así que un
    # DELETE físico debe fallar en vez de dejar variantes vendiendo sin descontar.
    option_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("option_groups.id"), nullable=False, index=True
    )
    option_group: Mapped["OptionGroup"] = relationship()

    min_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    max_select: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    #: Cantidad del insumo de **cada opción elegida**, no el total del grupo: elegir dos
    #: sabores con 120 descuenta 120 de cada uno (240 en total). Se suma a
    #: `options.item_quantity`, que es el consumo propio de la opción en todo el catálogo.
    #: 0 = el grupo se ofrece pero no descuenta por sí mismo.
    quantity_per_option: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        CheckConstraint("min_select >= 0", name="ck_variant_option_group_min_select"),
        CheckConstraint("max_select >= min_select", name="ck_variant_option_group_max_ge_min"),
        CheckConstraint(
            "quantity_per_option >= 0", name="ck_variant_option_group_qty_positive"
        ),
        UniqueConstraint(
            "product_variant_id", "option_group_id",
            name="uq__variant_option_groups__product_variant_id__option_group_id",
        ),
        {"schema": "tenant"},
    )
