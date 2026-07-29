from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import String, ForeignKey, DateTime, func
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .customer_order import CustomerOrder


class OrderCancelLog(UUIDPrimaryKeyMixin, Base):
    """Auditoría de cancelación de una orden (motivo + quién + cuándo).

    La cancelación revierte inventario **solo** de los ítems que cocina no llegó a
    preparar; lo ya consumido se registra como pérdida en `audit_logs`
    (`order.cancel.loss`). Ver `app/api/v1/orders/checkout.py:cancel_order`."""

    __tablename__ = "order_cancel_logs"

    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("customer_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped["CustomerOrder"] = relationship(back_populates="cancel_logs")

    motivo: Mapped[str] = mapped_column(String(500), nullable=False)

    # Referencia blanda a shared.users.id (quién canceló) + snapshot del nombre.
    # Null cuando canceló el propio comensal desde el QR: no es un usuario del
    # sistema. En ese caso `participant_id` dice quién fue.
    user_id: Mapped[Optional[UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    participant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("session_participants.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = ({"schema": "tenant"},)
