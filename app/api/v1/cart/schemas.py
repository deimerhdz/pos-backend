from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- Apertura de sesión (por QR token) ----------
class SessionOpenIn(BaseModel):
    qr_token: str = Field(..., description="Token de QR firmado de la mesa.")
    display_name: str = Field(
        ..., min_length=1, max_length=255, examples=["Ana Pérez"],
        description="Nombre que el comensal escribe. No es único ni identifica: "
                    "el identificador real va firmado en el token de sesión.",
    )


class SessionTableInfo(BaseModel):
    id: UUID
    number: int
    name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SessionOpenResponse(BaseModel):
    participant_id: UUID
    table_session_id: UUID
    display_name: str
    # Nombre desambiguado que ven cocina y staff ("Ana (2)" si ya había una Ana).
    display_label: str | None = None
    expires_at: datetime | None = None
    table: SessionTableInfo
    cart_id: UUID
    # Token que el frontend debe enviar en `x-session-token` para operar el carrito.
    session_token: str


# ---------- Ítems de carrito ----------
class CartItemIn(BaseModel):
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


class CartItemUpdate(BaseModel):
    quantity: int | None = Field(None, ge=1)
    option_ids: list[UUID] | None = None
    notes: str | None = Field(None, max_length=500)


class CartItemOptionResponse(BaseModel):
    id: UUID
    option_id: UUID

    model_config = ConfigDict(from_attributes=True)


class CartItemResponse(BaseModel):
    id: UUID
    product_variant_id: UUID
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    # Precio/subtotal ya con el mejor descuento percent/fixed vigente aplicado, o
    # `None` si ninguna promoción aplica a esta línea (o es un combo: ese ahorro se
    # calcula aparte, al cobrar).
    discounted_unit_price: Decimal | None = None
    discounted_line_total: Decimal | None = None
    notes: str | None = None
    combo_id: UUID | None = None
    options: list[CartItemOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CartResponse(BaseModel):
    id: UUID
    participant_id: UUID
    # Nombre del comensal dueño del carrito. Viaja aquí porque el `session_token`
    # no lleva el nombre: es lo que permite repintar el saludo tras una recarga.
    display_name: str
    display_label: str | None = None
    status: str
    total: Decimal
    # Suma de las líneas ya con su mejor descuento aplicado, o `None` si ninguna
    # línea tiene promoción vigente. Vista previa para el comensal; el cobro real
    # lo sigue fijando el checkout de staff.
    discounted_total: Decimal | None = None
    items: list[CartItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ---------- Pedidos del comensal ----------
class MyOrderCancelIn(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=500,
                        examples=["Me equivoqué de sabor"])


# ---------- Pagos del comensal (spec 024) ----------
class DinerPaymentMethod(BaseModel):
    """Método de pago tal como lo ve el comensal — solo los que el tenant
    tiene `active` (FR-004); nunca expone el flag `active` en sí."""
    id: UUID
    name: str
    type: str
    is_cash: bool
    payment_info: dict[str, str] | None = None
    # Metadata de formato de cada clave de `payment_info` (spec 034,
    # FR-011/FR-012) — mismo shape que ya usa `CatalogPaymentMethodOption.fields`
    # (sales/schemas.py:57): [{"key", "label", "required", "format", "length"?}].
    fields: list[dict] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class PaymentAttemptCreateIn(BaseModel):
    payment_method_id: UUID


class DinerPaymentAttempt(BaseModel):
    """Intento de pago tal como lo ve el comensal — **nunca** incluye
    `rejection_reason` (Clarification 3)."""
    id: UUID
    order_id: UUID
    payment_method_id: UUID
    status: str
    receipt_file_url: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReceiptPresignIn(BaseModel):
    content_type: str = Field(..., min_length=1, max_length=100, examples=["image/jpeg"])


class ReceiptPresignOut(BaseModel):
    upload_url: str
    key: str
    public_url: str
    expires_in: int


class ReceiptAttachIn(BaseModel):
    file_url: str = Field(..., min_length=1, max_length=500)


# ---------- Revisión y pago antes de enviar (spec 025) ----------
class SubmitCartIn(BaseModel):
    """Cuerpo de `POST /cart/submit` — el pedido nace junto con su primer
    intento de pago (contracts/submit-cart-with-payment.md)."""
    payment_method_id: UUID
    receipt_file_url: str | None = Field(None, max_length=500)


class PaymentReceiptPresignIn(ReceiptPresignIn):
    """Mismo shape que `ReceiptPresignIn` (`content_type`), sin campos
    nuevos — reexportada con su propio nombre porque alimenta un endpoint
    distinto (`POST /cart/payment-receipt/presign`, no ligado a ningún
    `attempt_id`, contracts/payment-receipt-presign.md)."""
