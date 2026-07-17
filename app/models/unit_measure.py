from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import String, Boolean


class UnitMeasure(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Unidad de medida simple (litro, gramo, ml, unidad, bola)."""

    __tablename__ = "unit_measures"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    abbreviation: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": "tenant"},)
