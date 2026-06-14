
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey,Integer
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from typing import TYPE_CHECKING,Optional

if TYPE_CHECKING:
    from app.models.product import Product

class Inventory(UUIDPrimaryKeyMixin,TimestampMixin,Base):
        __tablename__ = "inventory"
       
        stock: Mapped[int] = mapped_column(Integer,nullable=False)
        
        stock_min: Mapped[int] = mapped_column(Integer,nullable=False) 
        product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"))
        
        product:Mapped[Optional["Product"]] = relationship("Product", back_populates="inventory")
       
        __table_args__ = ({"schema": "tenant"},)