from uuid import UUID
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModifierCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150, examples=["Chispas de chocolate"])
    price: Decimal = Field(0, ge=0, max_digits=10, decimal_places=2, examples=["1000.00"])


class ModifierUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    price: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    active: bool | None = None


class ModifierResponse(BaseModel):
    id: UUID
    name: str
    price: Decimal
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ModifierGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150, examples=["Toppings"])
    required: bool = Field(False, examples=[False])
    min_select: int = Field(0, ge=0, examples=[0])
    max_select: int | None = Field(None, ge=1, examples=[3])
    modifiers: list[ModifierCreate] | None = Field(None, description="Modificadores iniciales.")

    @model_validator(mode="after")
    def _check_rules(self):
        if self.max_select is not None and self.min_select > self.max_select:
            raise ValueError("min_select cannot be greater than max_select")
        if self.required and self.min_select < 1:
            raise ValueError("a required group must have min_select >= 1")
        return self


class ModifierGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    required: bool | None = None
    min_select: int | None = Field(None, ge=0)
    max_select: int | None = Field(None, ge=1)
    active: bool | None = None


class ModifierGroupResponse(BaseModel):
    id: UUID
    name: str
    required: bool
    min_select: int
    max_select: int | None = None
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ModifierGroupDetailResponse(ModifierGroupResponse):
    modifiers: list[ModifierResponse] = Field(default_factory=list)
