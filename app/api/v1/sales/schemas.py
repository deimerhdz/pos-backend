from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Métodos de pago ----------
class PaymentMethodCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["Efectivo", "Nequi"])
    is_cash: bool = False


class PaymentMethodResponse(BaseModel):
    id: UUID
    name: str
    is_cash: bool
    active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Checkout ----------
class SaleItemIn(BaseModel):
    product_variant_id: UUID
    quantity: int = Field(1, ge=1)
    option_ids: list[UUID] = Field(default_factory=list)


class PaymentIn(BaseModel):
    payment_method_id: UUID
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    reference: str | None = Field(None, max_length=255)


class SaleCreate(BaseModel):
    cash_shift_id: UUID
    dining_session_id: UUID | None = None
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

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    id: UUID
    payment_method_id: UUID
    amount: Decimal
    reference: str | None = None

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
    status: str
    sold_at: datetime
    items: list[SaleItemResponse] = Field(default_factory=list)
    payments: list[PaymentResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
