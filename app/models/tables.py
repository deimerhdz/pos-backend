from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.orm  import mapped_column,Mapped
from sqlalchemy import String,Boolean

class Table(UUIDPrimaryKeyMixin,TimestampMixin,Base):
    
    __tablename__ = "tables"
    
    name : Mapped[str]  = mapped_column(String(255),nullable=False)
    
    qr_code : Mapped[str]  = mapped_column(String(255),nullable=True,unique=True,index=True)
   
    capacity : Mapped[int]  = mapped_column(nullable=False)
    
    status : Mapped[str]  = mapped_column(String(50),nullable=False,default="available")
    
    active:Mapped[bool] = mapped_column(Boolean, default=True)
    
    __table_args__ = ({"schema": "tenant"},)