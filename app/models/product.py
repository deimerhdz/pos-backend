
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from sqlalchemy import String,Boolean,ForeignKey,Numeric,CheckConstraint
from typing import TYPE_CHECKING,Optional
from decimal import Decimal

if TYPE_CHECKING:
    from .category import Category
    from .unit_measure import UnitMeasure
    from .inventory import Inventory
    from .product_component import ProductComponent

class Product(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "products"
    
    name: Mapped[str]= mapped_column(String(255),nullable=False)
    
    description: Mapped[Optional[str]] = mapped_column(String(255),nullable=True)
    
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2),nullable=False)
    
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 2),nullable=False)
    
    is_menu: Mapped[bool] = mapped_column(Boolean, default=False)
    
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    category:Mapped[Optional["Category"]] = relationship(back_populates="products")
    
    unit_measure_id: Mapped[str] = mapped_column(ForeignKey("unit_measures.id"))
    unit_measure:Mapped[Optional["UnitMeasure"]] = relationship(back_populates="products")
    
    active:Mapped[bool] = mapped_column(Boolean, default=True)

    product_type: Mapped[str] = mapped_column(String(50), nullable=False, server_default="PRODUCT")

    control_stock: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    inventory: Mapped[Optional["Inventory"]] = relationship(
        "Inventory", back_populates="product", uselist=False
    )

    components: Mapped[list["ProductComponent"]] = relationship(
        "ProductComponent",
        foreign_keys="ProductComponent.product_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "product_type IN ('INGREDIENT', 'PRODUCT', 'RECIPE')",
            name="ck_product_type",
        ),
        {"schema": "tenant"},
    )

    @property
    def stock(self) -> Optional[int]:
        return self.inventory.stock if self.inventory else None

    @property
    def stock_min(self) -> Optional[int]:
        return self.inventory.stock_min if self.inventory else None