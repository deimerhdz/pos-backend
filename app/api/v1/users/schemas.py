from enum import Enum
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RoleName(str, Enum):
    """Roles asignables por un admin de tenant (excluye SUPER_ADMIN)."""
    ADMIN = "ADMIN"
    CASHIER = "CASHIER"


class UserCreate(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=150,
        description="Nombre del usuario.",
        examples=["Cajero 1"],
    )
    email: EmailStr = Field(
        ...,
        description="Correo del usuario. Único dentro del tenant.",
        examples=["cajero1@acme.com"],
    )
    password: str = Field(
        ..., min_length=6, max_length=128,
        description="Contraseña en texto plano (se almacena hasheada).",
        examples=["secret123"],
    )
    phone: str | None = Field(
        None, max_length=20,
        description="Teléfono opcional del usuario.",
        examples=["3001234567"],
    )
    role: RoleName = Field(
        ...,
        description="Rol del usuario dentro del tenant: ADMIN o CASHIER.",
        examples=["CASHIER"],
    )


class UserRoleUpdate(BaseModel):
    role: RoleName = Field(
        ...,
        description="Nuevo rol del usuario: ADMIN o CASHIER.",
        examples=["ADMIN"],
    )


class UserStatusUpdate(BaseModel):
    active: bool = Field(
        ...,
        description="Nuevo estado del usuario: true=activo, false=inactivo.",
        examples=[False],
    )


class UserResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único del usuario.")
    name: str = Field(..., description="Nombre del usuario.")
    email: EmailStr = Field(..., description="Correo del usuario.")
    phone: str | None = Field(None, description="Teléfono del usuario.")
    active: bool = Field(..., description="Indica si el usuario está activo.")
    role_name: str | None = Field(None, description="Nombre del rol del usuario.")
    tenant_id: int | None = Field(None, description="Identificador del tenant al que pertenece.")
    tenant_name: str | None = Field(None, description="Nombre del tenant al que pertenece.")
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True)
