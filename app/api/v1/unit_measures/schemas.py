from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class UnitMeasureCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre de la unidad de medida.",
        examples=["Litro", "Gramo", "Bola"],
    )
    abbreviation: str = Field(
        ..., min_length=1, max_length=50,
        description="Abreviatura de la unidad de medida. Debe ser única.",
        examples=["L", "g", "bola"],
    )


class UnitMeasureUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    abbreviation: str | None = Field(None, min_length=1, max_length=50)
    active: bool | None = None


class UnitMeasureResponse(BaseModel):
    id: UUID
    name: str
    abbreviation: str
    active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
