from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import AccessTokenBearer, get_current_user
from app.core.crud import get_or_404, ensure_unique
from app.core.models import User
from app.core.pagination import Page, paginate
from app.models.presentation import Presentation
from app.api.v1.presentations.schemas import (
    PresentationCreate,
    PresentationUpdate,
    PresentationResponse,
)

router = APIRouter(prefix="/presentations", tags=["presentations"])
acccess_token_bearer = AccessTokenBearer()


@router.get(
    "",
    response_model=Page[PresentationResponse],
    summary="Listar presentaciones",
    description="Devuelve las presentaciones de forma paginada. Permite filtrar por estado activo/inactivo y buscar por nombre.",
    response_description="Página de presentaciones.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_presentations(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active: bool | None = Query(
        None, description="Filtra por estado activo (true) o inactivo (false)."
    ),
    search: str | None = Query(None, description="Búsqueda por nombre (contiene, sin distinguir mayúsculas)."),
    db: Session = Depends(get_db),
    _: dict = Depends(acccess_token_bearer),
    user: User = Depends(get_current_user),
):
    query = select(Presentation).order_by(Presentation.name)
    if active is not None:
        query = query.where(Presentation.active == active)
    if search:
        query = query.where(Presentation.name.ilike(f"%{search.strip()}%"))
    return paginate(db, query, page, size)


@router.get(
    "/{id}",
    response_model=PresentationResponse,
    summary="Obtener una presentación",
    description="Devuelve una presentación por su identificador único (UUID).",
    response_description="La presentación encontrada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La presentación no existe."},
    },
)
def get_presentation(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, Presentation, id, "Presentation not found")


@router.post(
    "",
    response_model=PresentationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una presentación",
    description="Crea una nueva presentación del catálogo global. Nace activa. El nombre debe ser único.",
    response_description="La presentación creada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        409: {"description": "Ya existe una presentación con ese nombre."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_presentation(
    body: PresentationCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ensure_unique(db, Presentation, Presentation.name, body.name, "Presentation name already exists")

    presentation = Presentation(name=body.name, active=True)
    db.add(presentation)
    db.commit()
    db.refresh(presentation)
    return presentation


@router.patch(
    "/{id}",
    response_model=PresentationResponse,
    summary="Actualizar una presentación",
    description="Actualiza parcialmente una presentación. Solo se modifican los campos enviados. Sin endpoint de borrado físico: el único ciclo de vida es renombrar y/o alternar `active` en cualquier sentido. Las variantes de producto no guardan el nombre: al renombrar, todas las que usan esta presentación lo reflejan de inmediato (spec 084, A-79).",
    response_description="La presentación actualizada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La presentación no existe."},
        409: {"description": "Ya existe una presentación con ese nombre."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_presentation(
    id: UUID,
    body: PresentationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    presentation = get_or_404(db, Presentation, id, "Presentation not found")

    if body.name is not None and body.name != presentation.name:
        ensure_unique(
            db, Presentation, Presentation.name, body.name,
            "Presentation name already exists", exclude_id=id,
        )
        # spec 084 (A-79): las variantes no guardan el nombre, lo leen de esta fila, así
        # que el renombre les llega solo -- sin cascada y sin choque posible con otras
        # variantes. Solo aplica la unicidad del catálogo (arriba).
        presentation.name = body.name

    if body.active is not None:
        presentation.active = body.active

    db.commit()
    db.refresh(presentation)
    return presentation
