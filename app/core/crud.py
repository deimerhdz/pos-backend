from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session


def get_or_404(db: Session, model: type, id: UUID, detail: str = "Not found"):
    """Devuelve la instancia por id o lanza 404."""
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return obj


def ensure_unique(
    db: Session,
    model: type,
    field: InstrumentedAttribute,
    value,
    detail: str = "Already exists",
    exclude_id: Optional[UUID] = None,
) -> None:
    """Lanza 409 si ya existe un registro de `model` con `field == value`.

    `exclude_id` excluye el propio registro (para updates).
    """
    existing = db.execute(
        select(model).where(field == value)
    ).scalar_one_or_none()
    if existing and (exclude_id is None or existing.id != exclude_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
