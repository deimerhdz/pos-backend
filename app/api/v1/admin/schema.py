from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel,Field

class TenantCreateWithUser(BaseModel):

    tenant_name:str = Field(...,min_length=3)
    schema_name:str = Field(...,min_length=3)
    host:str = Field(...,min_length=3)
    name:str = Field(...,min_length=3)
    email:str = Field(...,min_length=5)

    # spec 033, FR-004/FR-017: obligatorios, sin default — la creación de un
    # tenant no se completa sin elegir explícitamente un plan y su ciclo de
    # facturación (`ciclo_facturacion: null` es una elección válida de "sin
    # vencimiento", no un valor implícito, research.md Decisión 15).
    plan_id: UUID
    ciclo_facturacion: Optional[Literal["mensual", "anual"]]
