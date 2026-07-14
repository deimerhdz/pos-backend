from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .order_item import OrderItem


class CustomerOrder(UUIDPrimaryKeyMixin, Base):
    """Comanda. Puede venir por QR (ligada a una sesión de mesa) o por mostrador
    (con nombre suelto). `user_id` es null cuando el cliente pidió por QR."""

    __tablename__ = "customer_orders"

    dining_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_sessions.id"), nullable=True, index=True
    )

    dining_table_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=True
    )

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="qr")

    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="pending")

    # Referencia blanda a shared.users.id (null si el cliente pidió por QR).
    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('qr', 'counter', 'waiter')", name="ck_customer_order_channel"
        ),
        CheckConstraint(
            "status IN ('pending', 'preparing', 'served', 'cancelled')",
            name="ck_customer_order_status",
        ),
        {"schema": "tenant"},
    )
