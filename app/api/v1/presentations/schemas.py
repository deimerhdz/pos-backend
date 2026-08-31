from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PresentationCreate(BaseModel):
    # Se recortan espacios antes de validar: «8oz » no es una presentación
    # distinta de «8oz», y un nombre de solo espacios cae en 422 por min_length.
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=100, examples=["8oz", "16oz"])


class PresentationUpdate(BaseModel):
    """Parcial: renombrar / activar / desactivar. Enviar `active=false` mientras
    una regla de una promoción `active` la referencia devuelve 409 (FR-020)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=100)
    active: bool | None = None


class PresentationResponse(BaseModel):
    id: UUID
    name: str
    active: bool
    # Variantes ACTIVAS que referencian esta presentación — el alcance que verá
    # cualquier regla sobre ella (FR-005, panel "Productos Aplicables"). Default 0
    # para permitir `model_validate` directo desde el ORM; el router lo rellena.
    applicable_variant_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
