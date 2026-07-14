from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, Mapped, relationship
from sqlalchemy import String, Boolean, ForeignKey, CheckConstraint, UniqueConstraint
from typing import TYPE_CHECKING, Optional, List

if TYPE_CHECKING:
    from .category import Category
    from .product_variant import ProductVariant
    from .product_option_group import ProductOptionGroup


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Producto del menú. `preparation_type` = 'prepared' (se arma con receta)
    o 'packaged' (se vende empacado). El precio vive en la variante."""

    __tablename__ = "products"

    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id"), nullable=False, index=True
    )
    category: Mapped[Optional["Category"]] = relationship(back_populates="products")

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    preparation_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="prepared"
    )

    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    variants: Mapped[List["ProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    option_groups: Mapped[List["ProductOptionGroup"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "preparation_type IN ('prepared', 'packaged')",
            name="ck_product_preparation_type",
        ),
        UniqueConstraint("category_id", "name", name="uq__products__category_id__name"),
        {"schema": "tenant"},
    )
