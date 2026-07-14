from uuid import UUID
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Variantes ----------
class VariantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["1 bola", "2 bolas"])
    price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=100)


class VariantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    price: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    sku: str | None = Field(None, max_length=100)
    active: bool | None = None


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    name: str
    sku: str | None = None
    price: Decimal
    active: bool

    model_config = ConfigDict(from_attributes=True)


# ---------- Receta (BOM) ----------
class RecipeItemIn(BaseModel):
    inventory_item_id: UUID
    quantity: Decimal = Field(..., gt=0, max_digits=12, decimal_places=3)


class RecipeSet(BaseModel):
    items: list[RecipeItemIn] = Field(default_factory=list)


class RecipeItemResponse(BaseModel):
    id: UUID
    inventory_item_id: UUID
    quantity: Decimal

    model_config = ConfigDict(from_attributes=True)


# ---------- Grupos de opciones ----------
class OptionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Fresa", "Chocolate"])
    extra_price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    inventory_item_id: UUID | None = Field(
        None, description="Insumo que descuenta al elegir esta opción."
    )
    item_quantity: Decimal = Field(0, ge=0, max_digits=12, decimal_places=3)


class OptionResponse(BaseModel):
    id: UUID
    option_group_id: UUID
    name: str
    extra_price: Decimal
    inventory_item_id: UUID | None = None
    item_quantity: Decimal
    active: bool

    model_config = ConfigDict(from_attributes=True)


class OptionGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, examples=["Sabores de helado"])
    min_select: int = Field(0, ge=0)
    max_select: int = Field(1, ge=1)


class OptionGroupResponse(BaseModel):
    id: UUID
    name: str
    min_select: int
    max_select: int
    options: list[OptionResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ---------- Asignación grupo<->producto ----------
class ProductOptionGroupCreate(BaseModel):
    option_group_id: UUID
    min_select: int = Field(0, ge=0)
    max_select: int = Field(1, ge=1)


class ProductOptionGroupResponse(BaseModel):
    id: UUID
    product_id: UUID
    option_group_id: UUID
    min_select: int
    max_select: int

    model_config = ConfigDict(from_attributes=True)
