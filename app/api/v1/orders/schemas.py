from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.sales.schemas import PaymentIn


class OrderChannel(str, Enum):
    QR = "qr"
    COUNTER = "counter"
    WAITER = "waiter"


class OrderStatus(str, Enum):
    """Ciclo de pago de la orden (spec). El estado de cocina es por ítem."""
    ABIERTA = "abierta"
    BLOQUEADA = "bloqueada"
    PAGADA = "pagada"
    CANCELADA = "cancelada"


class KitchenStatus(str, Enum):
    """Estado de cocina (KDS) por ítem, independiente del status de pago."""
    PENDIENTE = "pendiente"
    EN_PREPARACION = "en_preparacion"
    LISTO = "listo"
    ENTREGADO = "entregado"
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


class TableQrTokenResponse(BaseModel):
    """Token firmado (tenant_id + table_id) para imprimir en el QR de la mesa,
    junto al path público del menú que lo consume."""
    table_id: UUID
    number: int
    qr_token: str
    menu_path: str


# ---------- Sesiones ----------
class SessionOpen(BaseModel):
    qr_token: UUID = Field(..., description="Token QR de la mesa escaneada.")
    customer_name: str = Field(..., min_length=1, max_length=255, examples=["Ana Pérez"])


class SessionResponse(BaseModel):
    id: UUID
    dining_table_id: UUID
    customer_name: str
    status: str
    opened_at: datetime
    closed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------- Comandas ----------
class OrderItemIn(BaseModel):
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)
    option_ids: list[UUID] = Field(default_factory=list)
    notes: str | None = Field(None, max_length=500)


class OrderCreate(BaseModel):
    channel: OrderChannel = OrderChannel.QR
    dining_session_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)
    items: list[OrderItemIn] = Field(..., min_length=1)


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class OrderItemOptionResponse(BaseModel):
    id: UUID
    option_id: UUID

    model_config = ConfigDict(from_attributes=True)


class OrderItemResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    session_id: UUID | None = None
    quantity: int
    unit_price: Decimal
    estado_cocina: str
    void_de: UUID | None = None
    notes: str | None = None
    options: list[OrderItemOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: UUID
    channel: str
    status: str
    version: int
    dining_session_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = None
    notes: str | None = None
    created_at: datetime
    items: list[OrderItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ---------- KDS (cocina) ----------
class KitchenTransitionIn(BaseModel):
    estado_cocina: KitchenStatus


class KdsItemResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    quantity: int
    estado_cocina: str
    notes: str | None = None
    options: list[OrderItemOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class KdsOrderResponse(BaseModel):
    order_id: UUID
    dining_table_id: UUID | None = None
    table_number: int | None = None
    created_at: datetime
    items: list[KdsItemResponse] = Field(default_factory=list)


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
    session_id: UUID | None = None
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
    session_id: UUID | None = None
    customer_name: str | None = None
    subtotal: Decimal


class BillResponse(BaseModel):
    dining_table_id: UUID
    total: Decimal
    orders: list[BillOrderLine] = Field(default_factory=list)
    split: list[BillSessionLine] = Field(default_factory=list)
