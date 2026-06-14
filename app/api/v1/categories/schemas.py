from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre de la categoría. Debe ser único.",
        examples=["Bebidas"],
    )
    description: str | None = Field(
        None, max_length=255,
        description="Descripción opcional de la categoría.",
        examples=["Gaseosas, jugos y aguas"],
    )


class CategoryUpdate(BaseModel):
    name: str | None = Field(
        None, min_length=1, max_length=255,
        description="Nuevo nombre de la categoría. Debe seguir siendo único.",
        examples=["Bebidas frías"],
    )
    description: str | None = Field(
        None, max_length=255,
        description="Nueva descripción de la categoría.",
        examples=["Gaseosas, jugos y aguas"],
    )
    active: bool | None = Field(
        None,
        description="Estado activo/inactivo de la categoría.",
        examples=[True],
    )


class CategoryResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la categoría.")
    name: str = Field(..., description="Nombre de la categoría.", examples=["Bebidas"])
    description: str | None = Field(
        None, description="Descripción de la categoría.",
        examples=["Gaseosas, jugos y aguas"],
    )
    active: bool = Field(..., description="Indica si la categoría está activa.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
