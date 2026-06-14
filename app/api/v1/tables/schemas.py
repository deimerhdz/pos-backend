from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class TableCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="Nombre o identificación de la mesa.",
        examples=["Mesa 1"],
    )
    qr_code: str | None = Field(
        None, max_length=255,
        description="Código QR asociado a la mesa. Si se envía, debe ser único.",
        examples=["QR-MESA-1"],
    )
    capacity: int = Field(
        ..., ge=1,
        description="Capacidad de comensales de la mesa.",
        examples=[4],
    )
    status: str = Field(
        "available", max_length=50,
        description="Estado de la mesa (p. ej. available, occupied, reserved).",
        examples=["available"],
    )


class TableUpdate(BaseModel):
    name: str | None = Field(
        None, min_length=1, max_length=255,
        description="Nuevo nombre o identificación de la mesa.",
        examples=["Mesa 2"],
    )
    qr_code: str | None = Field(
        None, max_length=255,
        description="Nuevo código QR. Debe seguir siendo único.",
        examples=["QR-MESA-2"],
    )
    capacity: int | None = Field(
        None, ge=1,
        description="Nueva capacidad de comensales.",
        examples=[6],
    )
    status: str | None = Field(
        None, max_length=50,
        description="Nuevo estado de la mesa.",
        examples=["occupied"],
    )
    active: bool | None = Field(
        None,
        description="Estado activo/inactivo de la mesa.",
        examples=[True],
    )


class TableResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la mesa.")
    name: str = Field(..., description="Nombre o identificación de la mesa.", examples=["Mesa 1"])
    qr_code: str | None = Field(None, description="Código QR asociado a la mesa.", examples=["QR-MESA-1"])
    capacity: int = Field(..., description="Capacidad de comensales de la mesa.", examples=[4])
    status: str = Field(..., description="Estado actual de la mesa.", examples=["available"])
    active: bool = Field(..., description="Indica si la mesa está activa.", examples=[True])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
