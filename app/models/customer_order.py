from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, ForeignKey, DateTime, func, CheckConstraint, Index, text
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .order_item import OrderItem
    from .order_cancel_log import OrderCancelLog


class CustomerOrder(UUIDPrimaryKeyMixin, Base):
    """Orden de mesa (spec `orders`). Una o más por mesa a lo largo de su ciclo
    de vida, pero **solo una `abierta` por mesa a la vez** (índice parcial). El
    `status` es el ciclo de pago; el estado de cocina vive por ítem
    (`order_items.estado_cocina`). `user_id` es null cuando el cliente pidió por
    QR."""

    __tablename__ = "customer_orders"

    dining_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_sessions.id"), nullable=True, index=True
    )

    dining_table_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("dining_tables.id"), nullable=True
    )

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="qr")

    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="abierta")

    # Lock optimista para la transición abierta→bloqueada del cobro (Fase 7).
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Referencia blanda a shared.users.id (null si el cliente pidió por QR).
    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    items: Mapped[List["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    cancel_logs: Mapped[List["OrderCancelLog"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('qr', 'counter', 'waiter')", name="ck_customer_order_channel"
        ),
        CheckConstraint(
            "status IN ('abierta', 'bloqueada', 'pagada', 'cancelada')",
            name="ck_customer_order_status",
        ),
        # Solo una orden 'abierta' por mesa a la vez (decisión #5 del spec).
        # Los dining_table_id NULL (mostrador) no colisionan entre sí.
        Index(
            "idx_open_order_per_table",
            "dining_table_id",
            unique=True,
            postgresql_where=text("status = 'abierta'"),
        ),
        {"schema": "tenant"},
    )
