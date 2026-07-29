from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, Integer, ForeignKey, DateTime, func, CheckConstraint
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .order_item import OrderItem
    from .order_cancel_log import OrderCancelLog


class CustomerOrder(UUIDPrimaryKeyMixin, Base):
    """Pedido. Varios por mesa y por comensal a lo largo de una `table_session`.

    `status` es el ciclo del pedido, **no** el de cocina (ese vive por ítem, en
    `order_items.estado_cocina`):

        recibida → abierta → bloqueada → pagada
                 ↘  cancelada (terminal, desde cualquier estado no terminal)

    - `recibida`: el comensal la envió desde el QR pero **aún no descuenta stock**;
    - `abierta`: confirmada por staff — aquí y solo aquí se descontó el inventario;
    - `bloqueada`: congelada para cobro (lock optimista por `version`);
    - `pagada` / `cancelada`: terminales.

    `user_id` es null cuando el pedido lo envió el cliente por QR."""

    __tablename__ = "customer_orders"

    # Sesión de mesa a la que pertenece el pedido (null en mostrador).
    table_session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("table_sessions.id"), nullable=True, index=True
    )

    # Comensal que lo envió (null si lo creó el staff).
    participant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("session_participants.id"), nullable=True, index=True
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

    # Unión de mesas (RF-053): órdenes con el mismo grupo se cobran juntas.
    merged_group_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

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
            "status IN ('recibida', 'abierta', 'bloqueada', 'pagada', 'cancelada')",
            name="ck_customer_order_status",
        ),
        # Ya NO hay índice único de "una orden abierta por mesa": la mesa puede
        # tener varios pedidos simultáneos (uno por comensal, o varias rondas del
        # mismo). La agrupación para cobrar la da `table_session_id`.
        {"schema": "tenant"},
    )
