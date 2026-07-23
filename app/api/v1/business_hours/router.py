"""Horarios de atención (RF-073). CRUD por día de la semana; el admin fija la
semana completa con un PUT (upsert por día)."""
from datetime import time
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.business_hours import BusinessHours

router = APIRouter(prefix="/business-hours", tags=["business-hours"])


class BusinessHoursIn(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="0=lunes .. 6=domingo")
    open_time: time | None = None
    close_time: time | None = None
    closed: bool = False


class BusinessHoursResponse(BaseModel):
    id: UUID
    day_of_week: int
    open_time: time | None = None
    close_time: time | None = None
    closed: bool
    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[BusinessHoursResponse], summary="Listar horarios de atención")
def list_hours(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(
        select(BusinessHours).order_by(BusinessHours.day_of_week)
    ).scalars().all()


@router.put("", response_model=list[BusinessHoursResponse], summary="Fijar horarios (upsert por día)")
def set_hours(
    body: list[BusinessHoursIn],
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    existing = {
        h.day_of_week: h for h in db.execute(select(BusinessHours)).scalars()
    }
    for item in body:
        row = existing.get(item.day_of_week)
        if row is None:
            row = BusinessHours(day_of_week=item.day_of_week)
            db.add(row)
        row.open_time = item.open_time
        row.close_time = item.close_time
        row.closed = item.closed
    db.commit()
    return db.execute(
        select(BusinessHours).order_by(BusinessHours.day_of_week)
    ).scalars().all()
