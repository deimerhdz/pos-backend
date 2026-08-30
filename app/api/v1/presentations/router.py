"""CRUD del catálogo de presentaciones (spec 040, Incremento A).

Sin cambios en endpoints existentes salvo el campo `presentation_id` que gana el
payload de variante (`catalog/`). El alcance de una regla de promoción se
resuelve por `id`, así que **renombrar no está bloqueado** por uso — solo
desactivar y eliminar lo están mientras una promoción `active` la referencie
(FR-020).
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.crud import ensure_unique, get_or_404
from app.core.db import get_db
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.core.pagination import Page, paginate
from app.models.presentation import Presentation
from app.api.v1.presentations import service
from app.api.v1.presentations.schemas import (
    PresentationCreate, PresentationResponse, PresentationUpdate,
)

router = APIRouter(prefix="/presentations", tags=["presentations"])


def _to_response(db: Session, presentation: Presentation) -> dict:
    counts = service.applicable_variant_counts(db, [presentation.id])
    data = PresentationResponse.model_validate(
        presentation, from_attributes=True
    ).model_dump()
    data["applicable_variant_count"] = counts.get(presentation.id, 0)
    return data


@router.get("", response_model=Page[PresentationResponse], summary="Listar presentaciones")
def list_presentations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active: bool | None = Query(None, description="Filtra por estado activo/inactivo."),
    search: str | None = Query(None, description="Búsqueda por nombre."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = paginate(db, service.list_query(active=active, search=search), page, size)
    counts = service.applicable_variant_counts(db, [p.id for p in result["items"]])
    result["items"] = [
        {
            "id": p.id,
            "name": p.name,
            "active": p.active,
            "applicable_variant_count": counts.get(p.id, 0),
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in result["items"]
    ]
    return result


@router.post("", response_model=PresentationResponse,
             status_code=status.HTTP_201_CREATED, summary="Crear presentación")
def create_presentation(body: PresentationCreate, db: Session = Depends(get_db),
                        _: User = Depends(require_tenant_admin)):
    ensure_unique(db, Presentation, Presentation.name, body.name,
                  "Ya existe una presentación con ese nombre")
    presentation = service.create(db, body.name)
    db.commit()
    db.refresh(presentation)
    return _to_response(db, presentation)


@router.patch("/{presentation_id}", response_model=PresentationResponse,
              summary="Renombrar, activar o desactivar")
def update_presentation(presentation_id: UUID, body: PresentationUpdate,
                        db: Session = Depends(get_db),
                        _: User = Depends(require_tenant_admin)):
    presentation = get_or_404(db, Presentation, presentation_id, "Presentación no encontrada")
    if "name" in body.model_fields_set and body.name is not None:
        ensure_unique(db, Presentation, Presentation.name, body.name,
                      "Ya existe una presentación con ese nombre", exclude_id=presentation.id)
    presentation = service.update(db, presentation, body)
    db.commit()
    db.refresh(presentation)
    return _to_response(db, presentation)


@router.delete("/{presentation_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Eliminar presentación")
def delete_presentation(presentation_id: UUID, db: Session = Depends(get_db),
                        _: User = Depends(require_tenant_admin)):
    presentation = get_or_404(db, Presentation, presentation_id, "Presentación no encontrada")
    service.delete(db, presentation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
