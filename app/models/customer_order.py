from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import (
    String, Integer, Boolean, Numeric, ForeignKey, DateTime, func, CheckConstraint, Index, text,
)
from sqlalchemy.orm import mapped_column, Mapped, relationship
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .order_item import OrderItem
    from .order_cancel_log import OrderCancelLog
    from .order_payment_attempt import OrderPaymentAttempt


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

    # Canal de origen estandarizado (spec 055): POS/QR_MENU/WHATSAPP/API.
    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="POS")

    # Cómo se atiende el pedido (spec 055): DINE_IN/TAKEAWAY/DELIVERY. Nulable:
    # los pedidos de antes de esta mejora sin mesa quedan sin clasificar
    # (data-model.md, backfill) — todo pedido nuevo lo trae siempre asignado.
    order_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    # Técnico, no expuesto en ningún schema de API (research.md D2): distingue
    # las órdenes que abre/reusa `orders.consolidation.get_or_create_open_order`
    # (mesero) de las que arma `orders.service.create_order` (staff, POS) —
    # antes de esta spec esa distinción vivía en el propio `channel`
    # ('waiter' vs 'counter'); fusionarlos en un único valor `POS` de canal
    # exige preservarla aquí para no reabrir por accidente una comanda ya
    # cobrada (`checkout_and_send` deja las órdenes pagadas en `'abierta'`).
    is_consolidation_order: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # Spec 056 — solo diligenciados cuando order_type == 'DELIVERY'. Sin default
    # de ningún tipo (ni de columna ni de aplicación): un pedido a domicilio
    # incompleto no es un pedido válido, no un pedido con "$0"/"" implícito
    # (spec.md FR-006, Edge Cases).
    delivery_address: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    delivery_phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    delivery_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="abierta")

    # spec 063 (FR-021, A-64): hoy `CustomerOrder` no tiene ningún campo de
    # descuento. Se fija en el cobro (`pay_order`, `_close_unified`, `_close_split`)
    # con el mismo agregado que la `Sale`. Nace `0` para todo pedido existente.
    discount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    # spec 063 (FR-021, A-64): mismo contenido que `sales.applied_promotions`,
    # fijado en el cobro. Nace `'[]'`.
    applied_promotions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

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

    # Historial completo de intentos de pago (spec 024, FR-016) — nunca se
    # borran, solo lectura desde aquí.
    payment_attempts: Mapped[List["OrderPaymentAttempt"]] = relationship(
        back_populates="order"
    )

    @property
    def current_payment_attempt(self) -> Optional["OrderPaymentAttempt"]:
        """El intento de pago más reciente (o `None` si no hay ninguno) —
        base de `OrderResponse.current_payment_attempt` (spec 024). No es lo
        mismo que "el confirmado": mientras no exista uno con
        `status == 'confirmado'`, la orden sigue pendiente de pago para el
        comensal, sin importar cuántos intentos rechazados haya antes."""
        if not self.payment_attempts:
            return None
        return max(self.payment_attempts, key=lambda a: a.created_at)

    __table_args__ = (
        CheckConstraint(
            "channel IN ('POS', 'QR_MENU', 'WHATSAPP', 'API')", name="ck_customer_order_channel"
        ),
        CheckConstraint(
            "order_type IS NULL OR order_type IN ('DINE_IN', 'TAKEAWAY', 'DELIVERY')",
            name="ck_customer_order_order_type",
        ),
        CheckConstraint(
            "status IN ('recibida', 'abierta', 'bloqueada', 'pagada', 'cancelada')",
            name="ck_customer_order_status",
        ),
        CheckConstraint(
            "delivery_fee IS NULL OR delivery_fee >= 0",
            name="ck_customer_order_delivery_fee_non_negative",
        ),
        # spec 063 (FR-021, `063a`).
        CheckConstraint("discount >= 0", name="ck_customer_order_discount_non_negative"),
        Index("idx_customer_orders_channel", "channel"),
        Index("idx_customer_orders_order_type", "order_type"),
        # Ya NO hay índice único de "una orden abierta por mesa": la mesa puede
        # tener varios pedidos simultáneos (uno por comensal, o varias rondas del
        # mismo). La agrupación para cobrar la da `table_session_id`.
        #
        # A lo sumo una orden activa por comensal (spec 025, FR-013) — mismo
        # predicado que `_NON_TERMINAL_ORDER_STATUSES`
        # (`app/api/v1/cart/service.py`). Postgres no considera dos NULL
        # iguales: las órdenes de mostrador/mesero (participant_id NULL) no
        # se ven afectadas.
        Index(
            "idx_active_order_per_participant",
            "participant_id",
            unique=True,
            postgresql_where=text("status NOT IN ('pagada', 'cancelada')"),
        ),
        {"schema": "tenant"},
    )
