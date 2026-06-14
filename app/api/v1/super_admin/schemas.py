from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantResponse(BaseModel):
    id: int = Field(..., description="Identificador único del tenant.")
    name: str = Field(..., description="Nombre del tenant.", examples=["Acme"])
    schema_name: str = Field(
        ..., alias="schema",
        description="Schema de PostgreSQL asignado al tenant.",
        examples=["tenant_acme"],
    )
    host: str = Field(..., description="Host asociado al tenant.", examples=["acme.localhost"])
    plan: str = Field(..., description="Plan contratado por el tenant.", examples=["basic"])
    created_at: datetime = Field(..., description="Fecha de creación del registro.")
    updated_at: datetime | None = Field(None, description="Fecha de la última actualización.")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
