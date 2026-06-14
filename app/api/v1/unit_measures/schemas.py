from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class UnitMeasureCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre de la unidad de medida.",
        examples=["Kilogramo"],
    )
    abbreviation: str = Field(
        ..., min_length=1, max_length=50,
        description="Abreviatura de la unidad de medida. Debe ser única.",
        examples=["kg"],
    )


class UnitMeasureUpdate(BaseModel):
    name: str | None = Field(
        None, min_length=1, max_length=255,
        description="Nuevo nombre de la unidad de medida.",
        examples=["Gramo"],
    )
    abbreviation: str | None = Field(
        None, min_length=1, max_length=50,
        description="Nueva abreviatura. Debe seguir siendo única.",
        examples=["g"],
    )
    active: bool | None = Field(
        None,
        description="Estado activo/inactivo de la unidad de medida.",
        examples=[True],
    )


class UnitMeasureResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la unidad de medida.")
    name: str = Field(..., description="Nombre de la unidad de medida.", examples=["Kilogramo"])
    abbreviation: str = Field(..., description="Abreviatura de la unidad de medida.", examples=["kg"])
    active: bool = Field(..., description="Indica si la unidad de medida está activa.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
