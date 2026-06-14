from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import AccessTokenBearer, get_current_user
from app.core.crud import get_or_404, ensure_unique
from app.core.models import User
from app.models.category import Category
from app.api.v1.categories.schemas import CategoryCreate, CategoryUpdate, CategoryResponse

router = APIRouter(prefix="/categories", tags=["categories"])
acccess_token_bearer = AccessTokenBearer()

@router.get(
    "",
    response_model=list[CategoryResponse],
    summary="Listar categorías",
    description="Devuelve todas las categorías. Permite filtrar por estado activo/inactivo.",
    response_description="Lista de categorías.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_categories(
    active: bool | None = Query(
        None, description="Filtra por estado activo (true) o inactivo (false)."
    ),
    db: Session = Depends(get_db),
    _: dict = Depends(acccess_token_bearer),
    user: User = Depends(get_current_user),

):
    query = select(Category)
    if active is not None:
        query = query.where(Category.active == active)
    return db.execute(query).scalars().all()


@router.get(
    "/{id}",
    response_model=CategoryResponse,
    summary="Obtener una categoría",
    description="Devuelve una categoría por su identificador único (UUID).",
    response_description="La categoría encontrada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La categoría no existe."},
    },
)
def get_category(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, Category, id, "Category not found")


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una categoría",
    description="Crea una nueva categoría. El nombre debe ser único.",
    response_description="La categoría creada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        409: {"description": "Ya existe una categoría con ese nombre."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_category(
    body: CategoryCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    ensure_unique(db, Category, Category.name, body.name, "Category name already exists")

    category = Category(name=body.name, description=body.description)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch(
    "/{id}",
    response_model=CategoryResponse,
    summary="Actualizar una categoría",
    description="Actualiza parcialmente una categoría. Solo se modifican los campos enviados.",
    response_description="La categoría actualizada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La categoría no existe."},
        409: {"description": "Ya existe una categoría con ese nombre."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_category(
    id: UUID,
    body: CategoryUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    category = get_or_404(db, Category, id, "Category not found")

    if body.name is not None and body.name != category.name:
        ensure_unique(db, Category, Category.name, body.name, "Category name already exists")
        category.name = body.name

    if body.description is not None:
        category.description = body.description

    if body.active is not None:
        category.active = body.active

    db.commit()
    db.refresh(category)
    return category


@router.delete(
    "/{id}",
    response_model=CategoryResponse,
    summary="Desactivar una categoría",
    description="Realiza un borrado lógico (soft-delete): marca la categoría como inactiva (active=False) sin eliminarla físicamente.",
    response_description="La categoría desactivada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La categoría no existe."},
    },
)
def delete_category(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    category = get_or_404(db, Category, id, "Category not found")
    category.active = False
    db.commit()
    db.refresh(category)
    return category
