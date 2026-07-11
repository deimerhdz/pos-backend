from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttributeValueCreate(BaseModel):
    value: str = Field(
        ..., min_length=1, max_length=150,
        description="Valor del atributo (ej. Vainilla).",
        examples=["Vainilla"],
    )
    sort_order: int = Field(0, ge=0, description="Orden de presentación.", examples=[1])


class AttributeValueUpdate(BaseModel):
    value: str | None = Field(None, min_length=1, max_length=150)
    sort_order: int | None = Field(None, ge=0)
    active: bool | None = Field(None, description="Estado activo/inactivo del valor.")


class AttributeValueResponse(BaseModel):
    id: UUID
    value: str
    sort_order: int
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AttributeCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=150,
        description="Nombre del atributo. Único.",
        examples=["Sabor"],
    )
    affects_inventory: bool = Field(
        False,
        description="Si el atributo dispara consumo de receta (afecta inventario).",
        examples=[True],
    )
    values: list[str] | None = Field(
        None,
        description="Valores iniciales del atributo (opcional).",
        examples=[["Vainilla", "Chocolate", "Fresa"]],
    )


class AttributeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    affects_inventory: bool | None = None
    active: bool | None = None


class AttributeResponse(BaseModel):
    id: UUID
    name: str
    affects_inventory: bool
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AttributeDetailResponse(AttributeResponse):
    values: list[AttributeValueResponse] = Field(default_factory=list)
