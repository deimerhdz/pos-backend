from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class InventoryMovementCreate(BaseModel):
    quantity: int = Field(
        ..., gt=0,
        description="Cantidad de unidades a mover. Debe ser mayor a 0.",
        examples=[10],
    )
    reason: str | None = Field(
        None, max_length=255,
        description="Motivo opcional del movimiento.",
        examples=["Reposición de stock"],
    )
    reference_id: UUID | None = Field(
        None,
        description="Identificador opcional de un documento o entidad relacionada (p. ej. una orden).",
    )


class InventoryResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del inventario.")
    stock: int = Field(..., description="Stock actual del producto.", examples=[100])
    product_id: UUID = Field(..., description="Producto al que pertenece el inventario.")
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)


class InventoryMovementResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del movimiento.")
    quantity: int = Field(..., description="Cantidad de unidades movidas.", examples=[10])
    stock_before: int = Field(..., description="Stock antes del movimiento.", examples=[100])
    stock_after: int = Field(..., description="Stock después del movimiento.", examples=[110])
    type_movement: str = Field(
        ..., description="Tipo de movimiento: 'income' (entrada) o 'expense' (salida).",
        examples=["income"],
    )
    reference_id: UUID | None = Field(
        None, description="Identificador del documento o entidad relacionada.",
    )
    product_id: UUID = Field(..., description="Producto afectado por el movimiento.")
    reason: str | None = Field(None, description="Motivo del movimiento.", examples=["Reposición de stock"])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
