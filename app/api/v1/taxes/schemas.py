from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaxCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, examples=["Impoconsumo"])
    rate: Decimal = Field(..., ge=0, le=100, max_digits=5, decimal_places=2, examples=["8.00"])
    inclusive: bool = Field(False, description="Si el impuesto va incluido en el precio.", examples=[False])


class TaxUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    rate: Decimal | None = Field(None, ge=0, le=100, max_digits=5, decimal_places=2)
    inclusive: bool | None = None
    active: bool | None = None


class TaxResponse(BaseModel):
    id: UUID
    name: str
    rate: Decimal
    inclusive: bool
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class TaxLinkCreate(BaseModel):
    tax_id: UUID = Field(..., description="Impuesto a asociar.")
    product_id: UUID | None = Field(None, description="Producto objetivo (excluyente con variant_id).")
    variant_id: UUID | None = Field(None, description="Variante objetivo (excluyente con product_id).")

    @model_validator(mode="after")
    def _single_target(self):
        if (self.product_id is None) == (self.variant_id is None):
            raise ValueError("provide exactly one of product_id or variant_id")
        return self


class TaxLinkResponse(BaseModel):
    id: UUID
    tax_id: UUID
    product_id: UUID | None = None
    variant_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)
