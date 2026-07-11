
from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin
from sqlalchemy import String, Boolean, Numeric
from sqlalchemy.orm import mapped_column, Mapped
from decimal import Decimal


class Tax(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "taxes"

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    inclusive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": "tenant"},)
