from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CashMovementType(str, Enum):
    IN = "in"
    OUT = "out"


# ---------- Cajas ----------
class RegisterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Caja principal"])


class RegisterResponse(BaseModel):
    id: UUID
    name: str
    active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Turnos ----------
class ShiftOpen(BaseModel):
    cash_register_id: UUID
    opening_amount: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)


class DenominationIn(BaseModel):
    denomination: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(..., ge=0)


class ShiftClose(BaseModel):
    counted_amount: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    denominations: list[DenominationIn] = Field(default_factory=list)


class ShiftResponse(BaseModel):
    id: UUID
    cash_register_id: UUID
    user_id: UUID
    user_name: str | None = None
    opening_amount: Decimal
    opened_at: datetime
    closed_at: datetime | None = None
    counted_amount: Decimal | None = None
    status: str

    model_config = ConfigDict(from_attributes=True)


# ---------- Movimientos de efectivo ----------
class CashMovementIn(BaseModel):
    type: CashMovementType
    amount: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2)
    description: str = Field(..., min_length=1, max_length=255)


class CashMovementResponse(BaseModel):
    id: UUID
    cash_shift_id: UUID
    type: str
    amount: Decimal
    description: str
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Arqueo ----------
class ReconciliationResponse(BaseModel):
    cash_shift_id: UUID
    status: str
    opening_amount: Decimal
    cash_sales: Decimal
    cash_in: Decimal
    cash_out: Decimal
    expected: Decimal
    counted_amount: Decimal | None = None
    difference: Decimal | None = None
