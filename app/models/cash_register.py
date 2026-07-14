from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy import String, Boolean
from sqlalchemy.orm import mapped_column, Mapped


class CashRegister(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "cash_registers"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": "tenant"},)
