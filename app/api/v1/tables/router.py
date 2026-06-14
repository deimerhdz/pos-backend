from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.crud import get_or_404, ensure_unique
from app.core.models import User
from app.models.tables import Table
from app.api.v1.tables.schemas import TableCreate, TableUpdate, TableResponse

router = APIRouter(prefix="/tables", tags=["tables"])


@router.get(
    "",
    response_model=list[TableResponse],
    summary="Listar mesas",
    description="Devuelve todas las mesas. Permite filtrar por estado activo/inactivo.",
    response_description="Lista de mesas.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_tables(
    active: bool | None = Query(
        None, description="Filtra por estado activo (true) o inactivo (false)."
    ),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Table)
    if active is not None:
        query = query.where(Table.active == active)
    return db.execute(query).scalars().all()


@router.get(
    "/{id}",
    response_model=TableResponse,
    summary="Obtener una mesa",
    description="Devuelve una mesa por su identificador único (UUID).",
    response_description="La mesa encontrada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La mesa no existe."},
    },
)
def get_table(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, Table, id, "Table not found")


@router.post(
    "",
    response_model=TableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una mesa",
    description="Crea una nueva mesa. Si se envía un código QR, este debe ser único.",
    response_description="La mesa creada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        409: {"description": "Ya existe una mesa con ese código QR."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_table(
    body: TableCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    if body.qr_code is not None:
        ensure_unique(db, Table, Table.qr_code, body.qr_code, "Table qr_code already exists")

    table = Table(
        name=body.name,
        qr_code=body.qr_code,
        capacity=body.capacity,
        status=body.status,
    )
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@router.patch(
    "/{id}",
    response_model=TableResponse,
    summary="Actualizar una mesa",
    description="Actualiza parcialmente una mesa. Solo se modifican los campos enviados.",
    response_description="La mesa actualizada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La mesa no existe."},
        409: {"description": "Ya existe una mesa con ese código QR."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_table(
    id: UUID,
    body: TableUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    table = get_or_404(db, Table, id, "Table not found")

    if body.qr_code is not None and body.qr_code != table.qr_code:
        ensure_unique(db, Table, Table.qr_code, body.qr_code, "Table qr_code already exists")
        table.qr_code = body.qr_code

    if body.name is not None:
        table.name = body.name
    if body.capacity is not None:
        table.capacity = body.capacity
    if body.status is not None:
        table.status = body.status
    if body.active is not None:
        table.active = body.active

    db.commit()
    db.refresh(table)
    return table


@router.delete(
    "/{id}",
    response_model=TableResponse,
    summary="Desactivar una mesa",
    description="Realiza un borrado lógico (soft-delete): marca la mesa como inactiva (active=False) sin eliminarla físicamente.",
    response_description="La mesa desactivada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La mesa no existe."},
    },
)
def delete_table(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    table = get_or_404(db, Table, id, "Table not found")
    table.active = False
    db.commit()
    db.refresh(table)
    return table
