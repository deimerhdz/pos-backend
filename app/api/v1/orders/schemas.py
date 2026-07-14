from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class OrderChannel(str, Enum):
    QR = "qr"
    COUNTER = "counter"
    WAITER = "waiter"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PREPARING = "preparing"
    SERVED = "served"
    CANCELLED = "cancelled"


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

    model_config = ConfigDict(from_attributes=True)


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
    quantity: int
    unit_price: Decimal
    notes: str | None = None
    options: list[OrderItemOptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class OrderResponse(BaseModel):
    id: UUID
    channel: str
    status: str
    dining_session_id: UUID | None = None
    dining_table_id: UUID | None = None
    customer_name: str | None = None
    notes: str | None = None
    created_at: datetime
    items: list[OrderItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
