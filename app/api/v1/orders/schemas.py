from enum import Enum
from typing import Literal
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.sales.schemas import PaymentIn
from app.core.timezone import UtcDatetime


class OrderChannel(str, Enum):
    """Canal de origen del pedido, estandarizado (spec 055). `COUNTER`/`WAITER`
    (personal del punto de venta) y `QR` se fusionaron/renombraron: `POS`
    cubre tanto el mostrador/cajero como al mesero (Terminal de Mesas, modo
    híbrido) — ver `app/api/v1/orders/consolidation.py` para cómo se preserva
    esa distinción internamente sin exponerla aquí."""
    POS = "POS"
    QR_MENU = "QR_MENU"
    WHATSAPP = "WHATSAPP"
    API = "API"


class OrderType(str, Enum):
    """Cómo se atiende el pedido (spec 055)."""
    DINE_IN = "DINE_IN"
    TAKEAWAY = "TAKEAWAY"
    DELIVERY = "DELIVERY"


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
    channel: OrderChannel = OrderChannel.POS
    #: Cómo se atiende el pedido (spec 055). Validado contra `channel` en
    #: `orders.service.create_order` (no toda combinación tiene sentido de
    #: negocio — p. ej. WHATSAPP nunca admite DINE_IN).
    order_type: OrderType = OrderType.DINE_IN
    participant_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)
    items: list[OrderItemIn] = Field(..., min_length=1)
    #: Terminal de Mesas modo híbrido (spec 028): comanda de mostrador/mesero
    #: que nace en 'recibida' en lugar de 'abierta' — el staff cobra primero
    #: (`POST /orders/{id}/checkout-and-send`) y recién ahí se descuenta
    #: inventario y se envía a cocina. Solo aplica a `channel=POS`; combinado
    #: con `channel=QR_MENU` es 400 (ese canal ya tiene su propio flujo
    #: `recibida` vía `/cart/submit`).
    hold_for_payment: bool = False


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
    # Snapshot del descuento vigente al confirmar (spec 038, FR-013), mismos
    # nombres/semántica que `CartItemResponse`: `None` si ninguna promoción
    # aplicó a la línea (o es un combo), o si el pedido es anterior a esta
    # spec (columnas nuevas, sin backfill — FR-015).
    discounted_unit_price: Decimal | None = None
    discounted_line_total: Decimal | None = None
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
    order_type: str | None = None
    status: str
    version: int
    table_session_id: UUID | None = None
    participant_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = None
    notes: str | None = None
    created_at: UtcDatetime
    items: list[OrderItemResponse] = Field(default_factory=list)
    # Intento de pago más reciente (spec 024) — `None` si nunca se inició
    # ninguno. Mientras no haya uno `confirmado`, la orden sigue "pendiente de
    # pago" para el comensal (Key Entity `Orden`, no es una columna de status).
    current_payment_attempt: CurrentPaymentAttemptSummary | None = None
    # Computado (spec 029) — no es una columna: verdadero si ya existe una
    # `Sale` con `customer_order_id` igual al de esta orden. Es la señal real
    # de "ya está pagado": a diferencia de `status`, que nunca llega a
    # "pagada" en los caminos QR/mostrador vigentes. El router lo asigna
    # antes de serializar (`orders.service.order_has_sale`/`paid_order_ids`).
    paid: bool = False

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
    resolved_at: UtcDatetime | None = None
    created_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)


class PaymentAttemptRejectIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class PaymentAttemptApproveIn(BaseModel):
    """Spec 028: aprobar ya genera la venta/factura en la misma llamada, así
    que necesita el turno de caja donde registrarla — mismo campo que
    `PayIn`/`CheckoutAndSendIn`."""
    cash_shift_id: UUID


class PaymentAttemptConfirmCashIn(BaseModel):
    amount_received: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    # Spec 028: ver `PaymentAttemptApproveIn`.
    cash_shift_id: UUID


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


class CheckoutAndSendIn(BaseModel):
    """Cobra y envía a cocina, en un solo paso, una comanda creada con
    `hold_for_payment=True` (`POST /orders/{order_id}/checkout-and-send`).
    Mismos campos que `PayIn` más `version` (lock optimista, igual que
    `BlockIn`) y el nombre para la factura."""
    version: int = Field(..., ge=0, description="Versión esperada (lock optimista).")
    cash_shift_id: UUID
    # spec 029 (Historia 2, FR-009/010/011): descuento manual prohibido sin
    # excepción en la Terminal de Mesas — único valor válido es 0. El motor
    # de promociones (`promotions.evaluate`/`combo_discount_for_lines`) sigue
    # sumándose aparte en `checkout_and_send`, sin relación con este campo.
    # No se toca el `discount` compartido de `sales/schemas.py` (mostrador/
    # cierre unificado/dividido) — ese es alcance de spec 011.
    discount: Decimal = Field(0, ge=0, le=0, max_digits=12, decimal_places=2)
    tax: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    tip: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    payments: list[PaymentIn] = Field(..., min_length=1)
    billing_customer_name: str | None = Field(
        None, max_length=255,
        description="A nombre de quién va la factura. Si se omite, 'Consumidor Final'.",
    )


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
