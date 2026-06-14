from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.models import User
from app.core.pagination import Page, paginate
from app.api.v1.orders.service import OrderService
from app.api.v1.orders.schemas import OrderResponse, OrderStatus, OrderStatusUpdate

router = APIRouter(prefix="/orders", tags=["orders"])

service = OrderService()


@router.get(
    "",
    response_model=Page[OrderResponse],
    summary="Listar órdenes (staff)",
    description="Devuelve las órdenes de forma paginada. Permite filtrar por mesa y por estado.",
    response_description="Página de órdenes.",
    responses={
        401: {"description": "No autenticado o token inválido."},
    },
)
def list_orders(
    table_id: UUID | None = Query(None, description="Filtra por mesa."),
    status: OrderStatus | None = Query(None, description="Filtra por estado de la orden."),
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = service.list_query(table_id=table_id, status_filter=status.value if status else None)
    return paginate(db, stmt, page, size)


@router.get(
    "/{id}",
    response_model=OrderResponse,
    summary="Obtener una orden (staff)",
    description="Devuelve una orden por su identificador, con sus items.",
    response_description="La orden encontrada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La orden no existe."},
    },
)
def get_order(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_or_404(db, id)


@router.patch(
    "/{id}",
    response_model=OrderResponse,
    summary="Actualizar el estado de una orden (staff)",
    description="Cambia el estado de la orden (pending, in_progress, completed, cancelled).",
    response_description="La orden actualizada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La orden no existe."},
        422: {"description": "Estado inválido."},
    },
)
def update_order_status(
    id: UUID,
    body: OrderStatusUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.update_status(db, id, body.status.value)
