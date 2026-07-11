from app.core.models import Base,TimestampMixin,UUIDPrimaryKeyMixin
from sqlalchemy.orm  import mapped_column,Mapped,relationship
from sqlalchemy import String,Boolean,Numeric,CheckConstraint
from typing import List,TYPE_CHECKING
from decimal import Decimal

if TYPE_CHECKING:
    from .product import Product

class UnitMeasure(UUIDPrimaryKeyMixin,TimestampMixin,Base):

    __tablename__ = "unit_measures"

    name : Mapped[str]  = mapped_column(String(255),nullable=False)

    abbreviation : Mapped[str]  = mapped_column(String(50),nullable=False,unique=True,index=True)

    # Fase 1: dimensión física + factor a la unidad base (para validar/convertir recetas).
    dimension: Mapped[str] = mapped_column(String(20), nullable=False, server_default="COUNT")

    factor_to_base: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=1, server_default="1"
    )

    active:Mapped[bool] = mapped_column(Boolean, default=True)

    products:Mapped[List["Product"]] = relationship(back_populates="unit_measure")

    __table_args__ = (
        CheckConstraint(
            "dimension IN ('MASS', 'VOLUME', 'COUNT')",
            name="ck_unit_measure_dimension",
        ),
        {"schema": "tenant"},
    )