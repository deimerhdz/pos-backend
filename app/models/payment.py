from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Boolean, Numeric, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from decimal import Decimal
from datetime import datetime

if TYPE_CHECKING:
    from .sale import Sale


class PaymentMethod(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payment_methods"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    is_cash: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = ({"schema": "tenant"},)


class Payment(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "payments"

    sale_id: Mapped[UUID] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sale: Mapped["Sale"] = relationship(back_populates="payments")

    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_methods.id"), nullable=False, index=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    paid_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_amount_positive"),
        {"schema": "tenant"},
    )
