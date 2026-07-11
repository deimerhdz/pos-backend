from uuid import UUID
from datetime import datetime, date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------- supplies ----------
class SupplyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150, examples=["Base de vainilla"])
    unit_measure_id: UUID = Field(..., description="Unidad base del insumo (g, ml, und).")
    stock_min: Decimal = Field(0, ge=0, max_digits=14, decimal_places=3, examples=["1000"])
    track_expiry: bool = Field(False, description="Controla vencimiento (lácteos).", examples=[True])


class SupplyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    stock_min: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=3)
    track_expiry: bool | None = None
    active: bool | None = None


class SupplyResponse(BaseModel):
    id: UUID
    name: str
    unit_measure_id: UUID
    stock_current: Decimal
    stock_min: Decimal
    track_expiry: bool
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------- batches ----------
class SupplyBatchCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=100, examples=["L-2605"])
    quantity: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3, examples=["4700"])
    expires_at: date | None = Field(None, examples=["2026-08-15"])
    unit_cost: Decimal = Field(0, ge=0, max_digits=12, decimal_places=4, examples=["6"])
    received_at: date | None = Field(None, description="Por defecto hoy.")


class SupplyBatchResponse(BaseModel):
    id: UUID
    supply_id: UUID
    code: str
    quantity: Decimal
    expires_at: date | None = None
    unit_cost: Decimal
    received_at: date
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# ---------- movements (adjust/waste) ----------
class SupplyMovementCreate(BaseModel):
    type: str = Field(..., pattern="^(adjust|waste)$", examples=["waste"])
    quantity: Decimal = Field(
        ..., max_digits=14, decimal_places=3,
        description="waste: positivo (resta). adjust: delta con signo (+/-).",
        examples=["50"],
    )
    reason: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def _check(self):
        if self.type == "waste" and self.quantity <= 0:
            raise ValueError("waste quantity must be > 0")
        return self


class SupplyMovementResponse(BaseModel):
    id: UUID
    supply_id: UUID
    batch_id: UUID | None = None
    quantity: Decimal
    type: str
    reference_id: UUID | None = None
    reason: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- alerts ----------
class SupplyAlert(BaseModel):
    supply_id: UUID
    name: str
    stock_current: Decimal
    stock_min: Decimal


class ExpiringBatch(BaseModel):
    supply_id: UUID
    supply_name: str
    batch_id: UUID
    code: str
    quantity: Decimal
    expires_at: date | None = None


class AlertsResponse(BaseModel):
    low_stock: list[SupplyAlert] = Field(default_factory=list)
    expiring: list[ExpiringBatch] = Field(default_factory=list)


# ---------- consumption ----------
class ConsumeLine(BaseModel):
    variant_id: UUID | None = None
    modifier_id: UUID | None = None
    quantity: int = Field(..., gt=0, examples=[2])

    @model_validator(mode="after")
    def _single_owner(self):
        if (self.variant_id is None) == (self.modifier_id is None):
            raise ValueError("provide exactly one of variant_id or modifier_id")
        return self


class ConsumeRequest(BaseModel):
    reference_id: UUID | None = Field(None, description="Ej. id de la orden.")
    lines: list[ConsumeLine] = Field(..., min_length=1)


class ConsumedSupply(BaseModel):
    supply_id: UUID
    name: str
    consumed: Decimal
    unit: str | None = None
    stock_after: Decimal
    below_min: bool


class ConsumeResponse(BaseModel):
    consumed: list[ConsumedSupply] = Field(default_factory=list)
    movements: int = 0
    alerts: list[ConsumedSupply] = Field(default_factory=list)


# ---------- recipes ----------
class RecipeItemIn(BaseModel):
    supply_id: UUID = Field(..., description="Insumo consumido.")
    quantity: Decimal = Field(..., gt=0, max_digits=14, decimal_places=3, examples=["150"])
    unit_measure_id: UUID = Field(..., description="Unidad del consumo (misma dimensión que el insumo).")


class RecipeUpsert(BaseModel):
    items: list[RecipeItemIn] = Field(..., min_length=1, description="Insumos de la receta.")
    is_resale: bool = Field(
        False,
        description=(
            "Reventa directa: receta 1:1 contra un único insumo. Fuerza quantity=1 y la "
            "unidad base del insumo (ignora quantity/unit enviados)."
        ),
    )


class RecipeItemOut(BaseModel):
    id: UUID
    supply_id: UUID
    supply_name: str | None = None
    quantity: Decimal
    unit_measure_id: UUID
    unit: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RecipeResponse(BaseModel):
    id: UUID
    variant_id: UUID | None = None
    modifier_id: UUID | None = None
    active: bool
    is_resale: bool = False
    items: list[RecipeItemOut] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
