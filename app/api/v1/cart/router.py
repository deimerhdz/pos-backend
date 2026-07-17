"""Carrito por comensal (Fase 3). Flujo público del QR: sin auth de negocio.

`POST /cart/sessions` autentica por el token de QR firmado; el resto de
operaciones por el token de sesión (`x-session-token`) vía get_session_context.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.core.qr_context import open_qr_context, get_session_context, SessionContext
from app.models.dining_table import DiningTable
from app.api.v1.cart import service
from app.api.v1.cart.schemas import (
    SessionOpenIn, SessionOpenResponse,
    CartItemIn, CartItemUpdate, CartResponse,
)

router = APIRouter(prefix="/cart", tags=["cart"])


@router.post(
    "/sessions",
    response_model=SessionOpenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir sesión de comensal por QR (devuelve token de sesión)",
)
def open_session(body: SessionOpenIn):
    with open_qr_context(body.qr_token) as ctx:
        table = ctx.db.execute(
            select(DiningTable).where(
                DiningTable.id == ctx.table_id, DiningTable.active.is_(True)
            )
        ).scalar_one_or_none()
        if table is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
        return service.open_session(ctx.db, ctx.tenant.id, table, body.customer_name)


@router.get("", response_model=CartResponse, summary="Ver carrito de la sesión")
def get_cart(ctx: SessionContext = Depends(get_session_context)):
    return service.get_cart(ctx.db, ctx.session.id)


@router.post(
    "/items",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar línea al carrito",
)
def add_item(body: CartItemIn, ctx: SessionContext = Depends(get_session_context)):
    return service.add_item(ctx.db, ctx.session.id, body)


@router.patch(
    "/items/{item_id}", response_model=CartResponse, summary="Editar línea del carrito"
)
def update_item(
    item_id: UUID, body: CartItemUpdate,
    ctx: SessionContext = Depends(get_session_context),
):
    return service.update_item(ctx.db, ctx.session.id, item_id, body)


@router.delete(
    "/items/{item_id}", response_model=CartResponse, summary="Quitar línea del carrito"
)
def remove_item(item_id: UUID, ctx: SessionContext = Depends(get_session_context)):
    return service.remove_item(ctx.db, ctx.session.id, item_id)
