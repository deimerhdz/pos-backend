from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import ForeignKey, String, DateTime, func, CheckConstraint, Index, text
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import List, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from .cart_item import CartItem


class Cart(UUIDPrimaryKeyMixin, Base):
    """Carrito por comensal (1 por sesión de mesa). El comensal agrega ítems
    mientras la sesión está activa; al consolidar (Fase 4) sus líneas se copian
    a `order_items`. Un carrito 'abandonado' (sesión expirada) nunca se
    consolida y no toca inventario."""

    __tablename__ = "carts"

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("dining_sessions.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="abierto")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    items: Mapped[List["CartItem"]] = relationship(
        back_populates="cart", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('abierto', 'confirmado', 'abandonado')", name="ck_cart_status"
        ),
        # Solo un carrito 'abierto' por sesión a la vez.
        Index(
            "idx_open_cart_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'abierto'"),
        ),
        {"schema": "tenant"},
    )
