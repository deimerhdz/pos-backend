
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey,Integer,String,CheckConstraint
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from typing import TYPE_CHECKING,Optional

if TYPE_CHECKING:
    from app.models.product import Product
    
class InventoryMovement(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    __tablename__ = "inventory_movements"
       
    quantity: Mapped[int] = mapped_column(Integer,nullable=False)
    
    stock_before: Mapped[int] = mapped_column(Integer,nullable=False)
    
    stock_after: Mapped[int] = mapped_column(Integer,nullable=False)
    
    type_movement: Mapped[str] = mapped_column(String(50), nullable=False)
    
    reference_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
       
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
    
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    product:Mapped[Optional["Product"]] = relationship("Product")
    
    __table_args__ = (CheckConstraint(
            "type_movement IN ('income', 'expense')",
            name="ck_type_movement"
        ),{"schema": "tenant"},)