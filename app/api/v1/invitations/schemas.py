from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.v1.users.schemas import RoleName


class InvitationCreate(BaseModel):
    email: EmailStr = Field(
        ...,
        description="Correo de la persona invitada. Único (por invitación pendiente o cuenta) dentro del tenant.",
        examples=["cajero1@acme.com"],
    )
    role: RoleName = Field(
        ...,
        description="Rol que tendrá la cuenta al consumir la invitación: ADMIN o CASHIER.",
        examples=["CASHIER"],
    )


class InvitationResponse(BaseModel):
    id: UUID = Field(..., description="Identificador único de la invitación.")
    email: EmailStr = Field(..., description="Correo de la persona invitada.")
    role_name: str = Field(..., description="Nombre del rol asignado.")
    sent_at: datetime = Field(..., description="Fecha del último envío (creación o reenvío).")

    model_config = ConfigDict(from_attributes=True)
