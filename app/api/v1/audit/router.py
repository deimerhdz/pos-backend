"""Consulta de la bitácora de auditoría (RF-076). Solo lectura, admin."""
from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import require_tenant_admin
from app.core.models import User
from app.core.pagination import Page, paginate
from app.models.audit_log import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditLogResponse(BaseModel):
    id: UUID
    action: str
    entity: str
    entity_id: UUID | None = None
    user_id: UUID | None = None
    user_name: str | None = None
    payload: dict | None = None
    at: datetime
    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=Page[AuditLogResponse], summary="Listar bitácora de auditoría")
def list_audit(
    entity: str | None = Query(None, description="Filtra por tipo de entidad."),
    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    stmt = select(AuditLog).order_by(AuditLog.at.desc())
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    return paginate(db, stmt, page, size)
