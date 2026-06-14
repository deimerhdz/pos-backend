from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from sqlalchemy import String,Boolean
from typing import List,TYPE_CHECKING

if TYPE_CHECKING:
    from .product import Product
    
class UnitMeasure(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    
    __tablename__ = "unit_measures"
    
    name : Mapped[str]  = mapped_column(String(255),nullable=False)
    
    abbreviation : Mapped[str]  = mapped_column(String(50),nullable=False,unique=True,index=True)
    
    active:Mapped[bool] = mapped_column(Boolean, default=True)
    
    products:Mapped[List["Product"]] = relationship(back_populates="unit_measure")
    
    __table_args__ = ({"schema": "tenant"},)