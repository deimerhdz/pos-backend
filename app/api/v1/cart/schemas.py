from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Apertura de sesión (por QR token) ----------
class SessionOpenIn(BaseModel):
    qr_token: str = Field(..., description="Token de QR firmado de la mesa (Fase 0).")
    customer_name: str = Field(..., min_length=1, max_length=255, examples=["Ana Pérez"])


class SessionTableInfo(BaseModel):
    id: UUID
    number: int
    name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SessionOpenResponse(BaseModel):
    session_id: UUID
    customer_name: str
    expires_at: datetime | None = None
    table: SessionTableInfo
    cart_id: UUID
    # Token que el frontend debe enviar en `x-session-token` para operar el carrito.
    session_token: str


# ---------- Ítems de carrito ----------
class CartItemIn(BaseModel):
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)
    option_ids: list[UUID] = Field(default_factory=list)
    notes: str | None = Field(None, max_length=500)


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
    notes: str | None = None
    options: list[CartItemOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CartResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: str
    total: Decimal
    items: list[CartItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
