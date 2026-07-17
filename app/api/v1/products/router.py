from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.pagination import Page, paginate
from app.core.models import User
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductDetailResponse,
)

router = APIRouter(prefix="/products", tags=["products"])

service = ProductService()


@router.get(
    "",
    response_model=Page[ProductListResponse],
    summary="Listar productos",
    description="Devuelve los productos de forma paginada (más recientes primero). Filtra por estado activo.",
    responses={401: {"description": "No autenticado o token inválido."}},
)
def list_products(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active: bool | None = Query(None, description="Filtra por estado activo/inactivo."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return paginate(db, service.list_query(active), page, size)


@router.get(
    "/{id}",
    response_model=ProductDetailResponse,
    summary="Obtener un producto",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto no existe."},
    },
)
def get_product(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_or_404(db, id)


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un producto",
    description="Crea un producto de catálogo. Un producto SIMPLE recibe una variante default.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La categoría o la unidad de medida no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    return service.create_product(db, body)


@router.patch(
    "/{id}",
    response_model=ProductResponse,
    summary="Actualizar un producto",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto, la categoría o la unidad de medida no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_product(
    id: UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    return service.update_product(db, id, body)


@router.put(
    "/{id}",
    response_model=ProductResponse,
    summary="Actualizar un producto (alias de PATCH)",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto, la categoría o la unidad de medida no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def replace_product(
    id: UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    return service.update_product(db, id, body)


@router.delete(
    "/{id}",
    response_model=ProductResponse,
    summary="Desactivar un producto",
    description="Borrado lógico: marca el producto como inactivo (active=False).",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto no existe."},
    },
)
def delete_product(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    return service.soft_delete(db, id)
