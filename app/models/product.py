
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from sqlalchemy import String,Boolean,ForeignKey,Numeric,CheckConstraint,UniqueConstraint
from typing import TYPE_CHECKING,Optional
from decimal import Decimal

if TYPE_CHECKING:
    from .category import Category
    from .unit_measure import UnitMeasure
    from .variant import Variant
    from .product_attribute import ProductAttribute
    from .product_modifier_group import ProductModifierGroup

class Product(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "products"

    name: Mapped[str]= mapped_column(String(255),nullable=False)

    description: Mapped[Optional[str]] = mapped_column(String(255),nullable=True)

    # Catálogo: SIMPLE (se vende tal cual) o CONFIGURABLE (por variantes).
    type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="SIMPLE")

    price: Mapped[Decimal] = mapped_column(Numeric(10, 2),nullable=False)

    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2),nullable=False)

    is_menu: Mapped[bool] = mapped_column(Boolean, default=False)

    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    category:Mapped[Optional["Category"]] = relationship(back_populates="products")

    unit_measure_id: Mapped[str] = mapped_column(ForeignKey("unit_measures.id"))
    unit_measure:Mapped[Optional["UnitMeasure"]] = relationship(back_populates="products")

    active:Mapped[bool] = mapped_column(Boolean, default=True)

    variants: Mapped[list["Variant"]] = relationship(
        "Variant", cascade="all, delete-orphan"
    )

    product_attributes: Mapped[list["ProductAttribute"]] = relationship(
        "ProductAttribute", cascade="all, delete-orphan"
    )

    modifier_groups: Mapped[list["ProductModifierGroup"]] = relationship(
        "ProductModifierGroup", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('SIMPLE', 'CONFIGURABLE')",
            name="ck_product_kind",
        ),
        UniqueConstraint("category_id", "name", name="uq__products__category_id__name"),
        {"schema": "tenant"},
    )
