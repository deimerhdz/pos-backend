from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.crud import get_or_404, ensure_unique
from app.core.models import User
from app.models.unit_measure import UnitMeasure
from app.api.v1.unit_measures.schemas import UnitMeasureCreate, UnitMeasureUpdate, UnitMeasureResponse

router = APIRouter(prefix="/unit-measures", tags=["unit_measures"])


@router.get(
    "",
    response_model=list[UnitMeasureResponse],
    summary="Listar unidades de medida",
    description="Devuelve todas las unidades de medida. Permite filtrar por estado activo/inactivo.",
    response_description="Lista de unidades de medida.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_unit_measures(
    active: bool | None = Query(
        None, description="Filtra por estado activo (true) o inactivo (false)."
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(UnitMeasure)
    if active is not None:
        query = query.where(UnitMeasure.active == active)
    return db.execute(query).scalars().all()


@router.get(
    "/{id}",
    response_model=UnitMeasureResponse,
    summary="Obtener una unidad de medida",
    description="Devuelve una unidad de medida por su identificador único (UUID).",
    response_description="La unidad de medida encontrada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La unidad de medida no existe."},
    },
)
def get_unit_measure(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, UnitMeasure, id, "Unit measure not found")


@router.post(
    "",
    response_model=UnitMeasureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una unidad de medida",
    description="Crea una nueva unidad de medida. La abreviatura debe ser única.",
    response_description="La unidad de medida creada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        409: {"description": "Ya existe una unidad de medida con esa abreviatura."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_unit_measure(
    body: UnitMeasureCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ensure_unique(db, UnitMeasure, UnitMeasure.abbreviation, body.abbreviation, "Unit measure abbreviation already exists")

    unit_measure = UnitMeasure(name=body.name, abbreviation=body.abbreviation)
    db.add(unit_measure)
    db.commit()
    db.refresh(unit_measure)
    return unit_measure


@router.patch(
    "/{id}",
    response_model=UnitMeasureResponse,
    summary="Actualizar una unidad de medida",
    description="Actualiza parcialmente una unidad de medida. Solo se modifican los campos enviados.",
    response_description="La unidad de medida actualizada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La unidad de medida no existe."},
        409: {"description": "Ya existe una unidad de medida con esa abreviatura."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_unit_measure(
    id: UUID,
    body: UnitMeasureUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    unit_measure = get_or_404(db, UnitMeasure, id, "Unit measure not found")

    if body.abbreviation is not None and body.abbreviation != unit_measure.abbreviation:
        ensure_unique(db, UnitMeasure, UnitMeasure.abbreviation, body.abbreviation, "Unit measure abbreviation already exists")
        unit_measure.abbreviation = body.abbreviation

    if body.name is not None:
        unit_measure.name = body.name

    if body.active is not None:
        unit_measure.active = body.active

    db.commit()
    db.refresh(unit_measure)
    return unit_measure


@router.delete(
    "/{id}",
    response_model=UnitMeasureResponse,
    summary="Desactivar una unidad de medida",
    description="Realiza un borrado lógico (soft-delete): marca la unidad de medida como inactiva (active=False) sin eliminarla físicamente.",
    response_description="La unidad de medida desactivada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La unidad de medida no existe."},
    },
)
def delete_unit_measure(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    unit_measure = get_or_404(db, UnitMeasure, id, "Unit measure not found")
    unit_measure.active = False
    db.commit()
    db.refresh(unit_measure)
    return unit_measure
