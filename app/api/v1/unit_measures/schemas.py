from enum import Enum
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class Dimension(str, Enum):
    MASS = "MASS"
    VOLUME = "VOLUME"
    COUNT = "COUNT"


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
    dimension: Dimension = Field(
        Dimension.COUNT,
        description="Dimensión física: MASS, VOLUME o COUNT.",
        examples=["MASS"],
    )
    factor_to_base: Decimal = Field(
        1, gt=0, max_digits=12, decimal_places=4,
        description="Factor de conversión a la unidad base de su dimensión (ej. kg→1000 g).",
        examples=["1000"],
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
    dimension: Dimension | None = Field(
        None, description="Dimensión física: MASS, VOLUME o COUNT.",
    )
    factor_to_base: Decimal | None = Field(
        None, gt=0, max_digits=12, decimal_places=4,
        description="Factor de conversión a la unidad base.",
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
    dimension: Dimension = Field(..., description="Dimensión física.", examples=["MASS"])
    factor_to_base: Decimal = Field(..., description="Factor de conversión a la unidad base.", examples=["1000"])
    active: bool = Field(..., description="Indica si la unidad de medida está activa.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
