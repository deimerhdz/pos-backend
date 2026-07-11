from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductAttributeAssign(BaseModel):
    attribute_ids: list[UUID] = Field(
        ..., min_length=1,
        description="Atributos que combina el producto configurable.",
    )


class VariantValueOut(BaseModel):
    id: UUID
    attribute_value_id: UUID
    value: str | None = None

    model_config = ConfigDict(from_attributes=True)


class VariantResponse(BaseModel):
    id: UUID
    product_id: UUID
    sku: str
    price: Decimal
    is_default: bool
    active: bool
    values: list[VariantValueOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class VariantUpdate(BaseModel):
    sku: str | None = Field(None, min_length=1, max_length=100)
    price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    active: bool | None = None


class VariantGenerateResponse(BaseModel):
    created: int = Field(..., description="Combinaciones nuevas creadas.")
    total: int = Field(..., description="Total de variantes del producto.")
    variants: list[VariantResponse] = Field(default_factory=list)


class ProductModifierGroupAssign(BaseModel):
    group_id: UUID = Field(..., description="Grupo de modificadores a asociar.")


class ProductModifierGroupResponse(BaseModel):
    id: UUID
    product_id: UUID
    group_id: UUID

    model_config = ConfigDict(from_attributes=True)


class ProductAttributeResponse(BaseModel):
    id: UUID
    product_id: UUID
    attribute_id: UUID

    model_config = ConfigDict(from_attributes=True)
