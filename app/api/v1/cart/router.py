"""Carrito borrador del comensal. Flujo público del QR: sin auth de negocio.

`POST /cart/sessions` autentica por el token de QR firmado; el resto de
operaciones por el token de sesión (`x-session-token`) vía get_session_context.
El tenant y la mesa salen siempre del token firmado, nunca de un id del body.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select

from app.core.qr_context import (
    open_qr_context,
    open_session_context,
    get_session_context,
    SessionContext,
)
from app.core.rate_limit import enforce as rate_limit
from app.models.dining_table import DiningTable
from app.api.v1.cart import service
from app.api.v1.orders.schemas import OrderResponse
from app.api.v1.cart.schemas import (
    SessionOpenIn, SessionOpenResponse,
    CartItemIn, CartItemUpdate, CartResponse, MyOrderCancelIn,
)

router = APIRouter(prefix="/cart", tags=["cart"])


@router.post(
    "/sessions",
    response_model=SessionOpenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Unirse a la mesa por QR (devuelve token de sesión)",
)
async def open_session(body: SessionOpenIn, request: Request):
    # Por IP ANTES de verificar el token: si no, una ráfaga de tokens basura se
    # va en 401 sin pasar nunca por el limitador, que es justo el caso a frenar.
    await rate_limit(request, "cart_session")
    with open_qr_context(body.qr_token) as ctx:
        # Y por mesa después, con el table_id del token ya verificado (nunca del body).
        await rate_limit(request, "cart_session", table_id=ctx.table_id)
        table = ctx.db.execute(
            select(DiningTable).where(
                DiningTable.id == ctx.table_id, DiningTable.active.is_(True)
            )
        ).scalar_one_or_none()
        if table is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
        return service.open_session(ctx.db, ctx.tenant.id, table, body.display_name)


@router.get("", response_model=CartResponse, summary="Ver carrito de la sesión")
def get_cart(ctx: SessionContext = Depends(get_session_context)):
    return service.get_cart(ctx.db, ctx.participant.id)


@router.post(
    "/items",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar línea al carrito",
)
async def add_item(
    body: CartItemIn, request: Request,
    ctx: SessionContext = Depends(get_session_context),
):
    await rate_limit(request, "cart_items", table_id=ctx.table_id)
    return service.add_item(ctx.db, ctx.participant.id, body)


@router.patch(
    "/items/{item_id}", response_model=CartResponse, summary="Editar línea del carrito"
)
def update_item(
    item_id: UUID, body: CartItemUpdate,
    ctx: SessionContext = Depends(get_session_context),
):
    return service.update_item(ctx.db, ctx.participant.id, item_id, body)


@router.delete(
    "/items/{item_id}", response_model=CartResponse, summary="Quitar línea del carrito"
)
def remove_item(item_id: UUID, ctx: SessionContext = Depends(get_session_context)):
    return service.remove_item(ctx.db, ctx.participant.id, item_id)


@router.post(
    "/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Salir de la mesa (libera la mesa si era el último y no pidió nada)",
)
def leave(x_session_token: str | None = Header(None, alias="x-session-token")):
    """El comensal se va.

    **Es idempotente y nunca falla con 401.** El comensal ya se está marchando: si
    su token caducó o la mesa se cobró mientras tanto, el resultado deseado —que no
    siga contando como presente— ya se cumplió, y devolverle un error solo le
    impediría cerrar la pantalla.

    Si era el último de la mesa y no dejó ningún pedido que cobrar, la mesa vuelve
    a `libre` en el acto en vez de esperar al barrido.
    """
    if not x_session_token:
        return None
    try:
        with open_session_context(x_session_token) as ctx:
            service.leave_session(ctx.db, ctx.participant)
    except HTTPException:
        pass  # token inválido/expirado: ya no ocupa la mesa, nada que hacer
    return None


# ---------------------------- Pedidos del comensal ----------------------------

@router.post(
    "/submit",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar el carrito como pedido (queda 'recibida', sin descontar stock)",
)
async def submit_cart(
    request: Request, ctx: SessionContext = Depends(get_session_context),
):
    """El pedido queda pendiente de que el staff lo confirme; hasta entonces no
    compromete inventario y el comensal puede cancelarlo sin coste."""
    await rate_limit(request, "cart_submit", table_id=ctx.table_id)
    return service.submit_cart(ctx.db, ctx.participant)


@router.get(
    "/orders",
    response_model=list[OrderResponse],
    summary="Mis pedidos en esta sesión (para restaurar la UI al reingresar)",
)
def my_orders(ctx: SessionContext = Depends(get_session_context)):
    return service.list_my_orders(ctx.db, ctx.participant.id)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Cancelar un pedido propio (solo antes de que cocina lo empiece)",
)
def cancel_my_order(
    order_id: UUID,
    body: MyOrderCancelIn,
    ctx: SessionContext = Depends(get_session_context),
):
    return service.cancel_my_order(ctx.db, ctx.participant, order_id, body.motivo)
