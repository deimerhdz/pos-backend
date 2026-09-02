
from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from typing import Optional,List,TYPE_CHECKING
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from sqlalchemy import String,Boolean,Integer,CheckConstraint


if TYPE_CHECKING:
    from .product import Product

class Category(UUIDPrimaryKeyMixin,TimestampMixin,Base):
   __tablename__ = "categories"

   name:Mapped[str]  = mapped_column(String(255),nullable=False,unique=True,index=True)

   description:Mapped[Optional[str]] =  mapped_column(String(255),nullable=True)

   active:Mapped[bool] = mapped_column(Boolean, default=True)

   display_order:Mapped[int] = mapped_column(Integer, nullable=False)

   products:Mapped[List["Product"]] = relationship(back_populates="category")

   __table_args__ = (
       CheckConstraint("display_order >= 0", name="ck_category_display_order_non_negative"),
       {"schema": "tenant"},
   )