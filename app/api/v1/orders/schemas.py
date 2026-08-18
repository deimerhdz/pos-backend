from enum import Enum
from typing import Literal
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.sales.schemas import PaymentIn


class OrderChannel(str, Enum):
    QR = "qr"
    COUNTER = "counter"
    WAITER = "waiter"


class OrderStatus(str, Enum):
    """Ciclo del pedido. El estado de cocina es por ítem (`estado_cocina`)."""
    #: Enviada por el comensal desde el QR; todavía no descuenta inventario.
    RECIBIDA = "recibida"
    #: Confirmada por staff: aquí se descontó el stock.
    ABIERTA = "abierta"
    BLOQUEADA = "bloqueada"
    PAGADA = "pagada"
    CANCELADA = "cancelada"


class KitchenStatus(str, Enum):
    """Estado de preparación por ítem, independiente del status de pago."""
    PENDIENTE = "pendiente"
    EN_PREPARACION = "en_preparacion"
    LISTO = "listo"
    ANULADO = "anulado"


# ---------- Mesas ----------
class TableCreate(BaseModel):
    number: int = Field(..., ge=1)
    name: str | None = Field(None, max_length=255)


class TableUpdate(BaseModel):
    name: str | None = Field(None, max_length=255)
    active: bool | None = None


class TableResponse(BaseModel):
    id: UUID
    number: int
    name: str | None = None
    qr_token: UUID
    active: bool
    status: str

    model_config = ConfigDict(from_attributes=True)


# ---------- Mesas avanzado (RF-051..053) ----------
class TableStatusUpdate(BaseModel):
    status: Literal["libre", "ocupada", "reservada", "pendiente_pago"]


class MoveOrderIn(BaseModel):
    dining_table_id: UUID


class MergeOrdersIn(BaseModel):
    order_ids: list[UUID] = Field(..., min_length=2)


class MergeResponse(BaseModel):
    merged_group_id: UUID
    order_ids: list[UUID]


class GroupBillOrderLine(BaseModel):
    order_id: UUID
    dining_table_id: UUID | None = None
    status: str
    subtotal: Decimal


class GroupBillResponse(BaseModel):
    merged_group_id: UUID
    total: Decimal
    orders: list[GroupBillOrderLine]


class TableQrTokenResponse(BaseModel):
    """Token firmado (tenant_id + table_id) para imprimir en el QR de la mesa,
    junto al path público del menú que lo consume."""
    table_id: UUID
    number: int
    qr_token: str
    menu_path: str


# ---------- Comandas ----------
class OrderItemIn(BaseModel):
    product_variant_id: UUID | None = None
    combo_id: UUID | None = None
    quantity: int = Field(1, ge=1)
    option_ids: list[UUID] = Field(default_factory=list)
    notes: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _one_of(self):
        if (self.product_variant_id is None) == (self.combo_id is None):
            raise ValueError("Cada ítem requiere product_variant_id o combo_id (no ambos)")
        if self.combo_id is not None and self.option_ids:
            raise ValueError("Los combos no admiten option_ids en esta versión")
        return self


class OrderCreate(BaseModel):
    channel: OrderChannel = OrderChannel.COUNTER
    participant_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)
    items: list[OrderItemIn] = Field(..., min_length=1)


class OrderItemOptionResponse(BaseModel):
    id: UUID
    option_id: UUID

    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    participant_id: UUID | None = None
    quantity: int
    unit_price: Decimal
    estado_cocina: str
    void_de: UUID | None = None
    notes: str | None = None
    combo_id: UUID | None = None
    options: list[OrderItemOptionResponse] = Field(default_factory=list)
    #: Versión del evento de tiempo real que emitió esta escritura. Solo lo
    #: rellena `PATCH /orders/items/{id}/kitchen`; el KDS lo usa para descartar
    #: eventos en vuelo que revertirían su parche optimista. `None` si el evento
    #: no llegó a publicarse (Redis caído): el cliente sigue funcionando, solo
    #: pierde el desempate y se apoya en el guard `busy`.
    rt_v: int | None = None

    model_config = ConfigDict(from_attributes=True)


class CurrentPaymentAttemptSummary(BaseModel):
    """Resumen del intento de pago vigente de una orden, para el comensal
    (spec 024). **Nunca** incluye `rejection_reason` (Clarification 3) — el
    detalle del motivo solo lo ve el cajero, vía
    `GET /orders/{order_id}/payment-attempts` (`PaymentAttemptResponse`)."""
    id: UUID
    status: str
    payment_method_name: str
    is_cash: bool
    receipt_file_url: str | None = None


class OrderResponse(BaseModel):
    id: UUID
    channel: str
    status: str
    version: int
    table_session_id: UUID | None = None
    participant_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = None
    notes: str | None = None
    created_at: datetime
    items: list[OrderItemResponse] = Field(default_factory=list)
    # Intento de pago más reciente (spec 024) — `None` si nunca se inició
    # ninguno. Mientras no haya uno `confirmado`, la orden sigue "pendiente de
    # pago" para el comensal (Key Entity `Orden`, no es una columna de status).
    current_payment_attempt: CurrentPaymentAttemptSummary | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------- Intentos de pago (spec 024) ----------
class PaymentAttemptResponse(BaseModel):
    """Vista de staff/cajero — a diferencia de `CurrentPaymentAttemptSummary`,
    **sí** incluye `rejection_reason` (FR-016)."""
    id: UUID
    order_id: UUID
    payment_method_id: UUID
    payment_method_name: str
    is_cash: bool
    status: str
    amount_received: Decimal | None = None
    change_amount: Decimal | None = None
    receipt_file_url: str | None = None
    rejection_reason: str | None = None
    resolved_by_user_id: UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentAttemptRejectIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class PaymentAttemptConfirmCashIn(BaseModel):
    amount_received: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)


# ---------- Preparación ----------
class KitchenTransitionIn(BaseModel):
    estado_cocina: KitchenStatus


# ---------- Anulación / reemplazo de ítem ----------
class VoidItemIn(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=500)
    replacement: OrderItemIn | None = None


# ---------- Cobro / cancelación (Fase 7) ----------
class BlockIn(BaseModel):
    version: int = Field(..., ge=0, description="Versión esperada (lock optimista).")


class CancelIn(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=500)


class PayIn(BaseModel):
    cash_shift_id: UUID
    discount: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    tax: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    tip: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    payments: list[PaymentIn] = Field(..., min_length=1)


class BillItemLine(BaseModel):
    order_item_id: UUID
    product_variant_id: UUID
    participant_id: UUID | None = None
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    estado_cocina: str


class BillOrderLine(BaseModel):
    order_id: UUID
    status: str
    subtotal: Decimal
    items: list[BillItemLine] = Field(default_factory=list)


class BillSessionLine(BaseModel):
    participant_id: UUID | None = None
    display_label: str | None = None
    subtotal: Decimal


class BillResponse(BaseModel):
    dining_table_id: UUID
    total: Decimal
    orders: list[BillOrderLine] = Field(default_factory=list)
    split: list[BillSessionLine] = Field(default_factory=list)
