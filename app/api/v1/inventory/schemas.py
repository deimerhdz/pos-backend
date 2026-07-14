from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class InventoryItemType(str, Enum):
    RAW_MATERIAL = "raw_material"
    PACKAGED = "packaged"


# ---------- Insumos ----------
class InventoryItemCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Leche entera", "Fresa"])
    unit_measure_id: UUID
    type: InventoryItemType = InventoryItemType.RAW_MATERIAL
    current_stock: Decimal = Field(0, ge=0, max_digits=12, decimal_places=3)
    min_stock: Decimal = Field(0, ge=0, max_digits=12, decimal_places=3)
    unit_cost: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)


class InventoryItemUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    unit_measure_id: UUID | None = None
    type: InventoryItemType | None = None
    min_stock: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=3)
    unit_cost: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    active: bool | None = None


class InventoryItemResponse(BaseModel):
    id: UUID
    name: str
    unit_measure_id: UUID
    type: InventoryItemType
    current_stock: Decimal
    min_stock: Decimal
    unit_cost: Decimal
    active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Ajustes ----------
class AdjustmentIn(BaseModel):
    signed_delta: Decimal = Field(
        ..., max_digits=12, decimal_places=3,
        description="Delta con signo: positivo suma, negativo resta.",
        examples=["-2.5"],
    )
    reason: str | None = Field(None, max_length=255, examples=["Merma por derrame"])


class MovementResponse(BaseModel):
    id: UUID
    inventory_item_id: UUID
    type: str
    quantity: Decimal
    reason: str | None = None
    reference_type: str | None = None
    reference_id: UUID | None = None
    moved_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------- Proveedores ----------
class SupplierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    tax_id: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)


class SupplierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    tax_id: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)
    active: bool | None = None


class SupplierResponse(BaseModel):
    id: UUID
    name: str
    tax_id: str | None = None
    phone: str | None = None
    email: str | None = None
    active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Compras ----------
class PurchaseItemIn(BaseModel):
    inventory_item_id: UUID
    quantity: Decimal = Field(..., gt=0, max_digits=12, decimal_places=3)
    unit_cost: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)


class PurchaseCreate(BaseModel):
    supplier_id: UUID | None = None
    invoice_number: str | None = Field(None, max_length=100)
    items: list[PurchaseItemIn] = Field(..., min_length=1)


class PurchaseItemResponse(BaseModel):
    id: UUID
    inventory_item_id: UUID
    quantity: Decimal
    unit_cost: Decimal

    model_config = ConfigDict(from_attributes=True)


class PurchaseResponse(BaseModel):
    id: UUID
    supplier_id: UUID | None = None
    invoice_number: str | None = None
    total: Decimal
    purchased_at: datetime
    items: list[PurchaseItemResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class LowStockResponse(BaseModel):
    id: UUID
    name: str
    current_stock: Decimal
    min_stock: Decimal

    model_config = ConfigDict(from_attributes=True)
