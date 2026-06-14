from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.pagination import Page, paginate
from app.core.models import User
from app.api.v1.products.service import ProductService
from app.api.v1.products.dependencies import get_product_service
from app.api.v1.products.schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
    ProductDetailResponse,
)

router = APIRouter(prefix="/products", tags=["products"])


@router.get(
    "",
    response_model=Page[ProductListResponse],
    summary="Listar productos",
    description="Devuelve los productos de forma paginada, ordenados por fecha de creación descendente. Permite filtrar por estado activo/inactivo.",
    response_description="Página de productos.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_products(
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    active: bool | None = Query(
        None, description="Filtra por estado activo (true) o inactivo (false)."
    ),
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    stmt = service.products.list_query(active)
    return paginate(db, stmt, page, size)


@router.get(
    "/{id}",
    response_model=ProductDetailResponse,
    summary="Obtener un producto",
    description="Devuelve un producto por su identificador único (UUID), incluyendo su stock y, si es una receta, sus componentes.",
    response_description="El producto encontrado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto no existe."},
    },
)
def get_product(
    id: UUID,
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    return service.products.get_with_components_or_404(db, id)


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un producto",
    description=(
        "Crea un producto aplicando la estrategia según su tipo: "
        "INGREDIENT inicializa inventario; PRODUCT inicializa inventario solo si "
        "control_stock=true; RECIPE no crea inventario y guarda sus componentes."
    ),
    response_description="El producto creado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La categoría, la unidad de medida o un componente no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    return service.create_product(db, body)


@router.put(
    "/{id}",
    response_model=ProductResponse,
    summary="Actualizar un producto",
    description=(
        "Actualiza un producto aplicando la estrategia de su tipo. Para RECIPE, "
        "si se envían componentes, reemplaza por completo los existentes. "
        "Solo se modifican los campos enviados."
    ),
    response_description="El producto actualizado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto, la categoría, la unidad de medida o un componente no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_product(
    id: UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    return service.update_product(db, id, body)


@router.patch(
    "/{id}",
    response_model=ProductResponse,
    summary="Actualizar parcialmente un producto",
    description="Alias parcial de la actualización. Delega en la misma lógica de estrategia que PUT.",
    response_description="El producto actualizado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto, la categoría, la unidad de medida o un componente no existen."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def patch_product(
    id: UUID,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    return service.update_product(db, id, body)


@router.delete(
    "/{id}",
    response_model=ProductResponse,
    summary="Desactivar un producto",
    description="Realiza un borrado lógico (soft-delete): marca el producto como inactivo (active=False) sin eliminarlo físicamente.",
    response_description="El producto desactivado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "El producto no existe."},
    },
)
def delete_product(
    id: UUID,
    db: Session = Depends(get_db),
    service: ProductService = Depends(get_product_service),
    _: User = Depends(get_current_user),
):
    return service.soft_delete(db, id)
