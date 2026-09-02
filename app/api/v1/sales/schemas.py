from enum import Enum
from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.catalog.schemas import OptionSelectionIn
from app.core.timezone import UtcDatetime


# ---------- Métodos de pago ----------
class PaymentMethodType(str, Enum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    OTHER = "other"


class PaymentMethodCreate(BaseModel):
    """Activa un método del catálogo del Super Admin para este tenant (spec
    032, FR-007/FR-011). `name`/`type`/`is_cash` ya no se aceptan aquí — se
    copian de `catalog_id` en `service.py` (research.md Decisión 5); un tenant
    no puede crear métodos fuera del catálogo."""

    catalog_id: UUID
    # Datos de integración del método (cuenta, celular, código...); validados
    # contra `catalog.fields` en `service.py` (FR-009). `None`/`{}` para
    # métodos sin campos (ej. Efectivo).
    payment_info: dict[str, str] | None = None


class PaymentMethodUpdate(BaseModel):
    payment_info: dict[str, str] | None = None
    active: bool | None = None


class PaymentMethodResponse(BaseModel):
    id: UUID
    catalog_id: UUID | None = None
    name: str
    type: str
    is_cash: bool
    active: bool
    is_complete: bool
    payment_info: dict[str, str] | None = None

    model_config = ConfigDict(from_attributes=True)


class CatalogPaymentMethodOption(BaseModel):
    """Una entrada del catálogo de plataforma, vista desde el Tenant Admin
    (`GET /sales/payment-methods/catalog`, FR-005/FR-006). `active` es el
    estado del catálogo (no el de la activación del tenant); `already_activated`
    indica si el tenant ya tiene una configuración para este `catalog_id`."""

    id: UUID
    name: str
    fields: list[dict]
    active: bool
    already_activated: bool

    model_config = ConfigDict(from_attributes=True)


class PaymentMethodCheckoutOption(BaseModel):
    """Lo que el cajero ve en la pantalla de cobro (FR-012a, clarificación
    2026-08-24 #1): nunca `payment_info` (cuenta, celular, QR — los "datos de
    integración" que la clarificación reserva al Tenant Admin). `is_cash`/
    `type` sí viajan: son clasificación operativa, no datos de integración —
    el checkout los necesita para decidir si calcula cambio (mismo criterio
    que ya usaba `payment-input.component.ts` antes de esta spec)."""

    id: UUID
    name: str
    is_cash: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Checkout ----------
class SaleItemIn(BaseModel):
    # spec 063 (FR-024): el mecanismo de combo se retira; `combo_id` ya no se acepta.
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)
    # spec 065: reemplaza `option_ids: list[UUID]` -- cada entrada trae su propia
    # cantidad elegida (default 1, el mismo significado que tenía "incluir este id").
    options: list[OptionSelectionIn] = Field(default_factory=list)


class PaymentIn(BaseModel):
    payment_method_id: UUID
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    reference: str | None = Field(None, max_length=255)


class SaleCreate(BaseModel):
    cash_shift_id: UUID
    participant_id: UUID | None = None
    table_session_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=255)
    discount: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    tax: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    tip: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    items: list[SaleItemIn] = Field(..., min_length=1)
    payments: list[PaymentIn] = Field(..., min_length=1)


class SaleItemResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    description: str
    options: list = Field(default_factory=list)
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    combo_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    id: UUID
    payment_method_id: UUID
    amount: Decimal
    reference: str | None = None
    paid_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True)


class SaleInvoiceRef(BaseModel):
    """Consecutivo fiscal de la venta. Es lo que debe imprimirse en el ticket."""
    prefix: str
    number: int

    model_config = ConfigDict(from_attributes=True)


class SaleTableRef(BaseModel):
    """Mesa cobrada. Null en ventas de mostrador."""
    id: UUID
    number: int
    name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    id: UUID
    cash_shift_id: UUID
    user_id: UUID
    user_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    tip: Decimal
    total: Decimal
    paid_amount: Decimal | None = None
    change_given: Decimal | None = None
    status: str
    sold_at: UtcDatetime
    items: list[SaleItemResponse] = Field(default_factory=list)
    payments: list[PaymentResponse] = Field(default_factory=list)
    # Para reconstruir el ticket completo fuera del momento del cobro.
    invoice: SaleInvoiceRef | None = None
    dining_table: SaleTableRef | None = None

    model_config = ConfigDict(from_attributes=True)
