from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import events
from app.core.db import get_db, get_tenant
from app.core.crud import get_or_404
from app.core.http_cache import json_or_304
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User, Tenant
from app.core.qr_token import mint_qr_token
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem
from app.api.v1.orders import service
from app.api.v1.orders.consolidation import consolidate_table, add_item_to_table
from app.api.v1.orders import kitchen
from app.api.v1.orders import checkout
from app.api.v1.orders import tables_advanced
from app.api.v1.sales.schemas import SaleResponse
from app.api.v1.orders.schemas import (
    TableCreate, TableUpdate, TableResponse, TableQrTokenResponse,
    OrderCreate, OrderResponse, OrderItemIn,
    OrderItemResponse, KdsOrderResponse, KitchenTransitionIn, VoidItemIn,
    BlockIn, CancelIn, PayIn, BillResponse,
    TableStatusUpdate, MoveOrderIn, MergeOrdersIn, MergeResponse, GroupBillResponse,
)

router = APIRouter(prefix="/orders", tags=["orders"])

# A nivel de módulo a propósito: `TypeAdapter` compila el esquema, y construirlo
# por request tiraría por tierra el ahorro que persigue el ETag.
_ORDERS_ADAPTER = TypeAdapter(list[OrderResponse])


def _load_order(db: Session, order_id: UUID) -> CustomerOrder:
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    return order


# ============================ Mesas (staff) ============================
@router.get("/tables", response_model=list[TableResponse], summary="Listar mesas")
def list_tables(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(DiningTable).order_by(DiningTable.number)).scalars().all()


@router.post("/tables", response_model=TableResponse, status_code=status.HTTP_201_CREATED, summary="Crear mesa (genera qr_token)")
def create_table(body: TableCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    dup = db.execute(select(DiningTable).where(DiningTable.number == body.number)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una mesa con ese número")
    table = DiningTable(number=body.number, name=body.name)
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@router.patch("/tables/{table_id}", response_model=TableResponse, summary="Actualizar mesa")
def update_table(
    table_id: UUID, body: TableUpdate,
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(table, k, v)
    db.commit()
    db.refresh(table)
    return table


# ============================ Mesas avanzado (RF-051..054) ============================
@router.patch("/tables/{table_id}/status", response_model=TableResponse,
              summary="Cambiar estado operativo de la mesa (RF-051)")
def set_table_status(
    table_id: UUID, body: TableStatusUpdate,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    table = tables_advanced.set_table_status(db, table_id, body.status)
    events.table_status_changed(
        tenant.id, dining_table_id=table.id, table_number=table.number, status=table.status,
    )
    return table


@router.post("/{order_id}/move", response_model=OrderResponse,
             summary="Cambiar una orden de mesa (RF-052)")
def move_order(
    order_id: UUID, body: MoveOrderIn,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    tables_advanced.move_order(db, order_id, body.dining_table_id)
    order = _load_order(db, order_id)
    # Cambian las dos mesas: la que suelta el pedido y la que lo recibe.
    for table in db.execute(
        select(DiningTable).where(DiningTable.id == body.dining_table_id)
    ).scalars():
        events.table_status_changed(
            tenant.id, dining_table_id=table.id, table_number=table.number,
            status=table.status,
        )
    return order


@router.post("/merge", response_model=MergeResponse,
             summary="Unir mesas en una sola cuenta (RF-053)")
def merge_orders(
    body: MergeOrdersIn,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    return tables_advanced.merge_orders(db, body.order_ids)


@router.get("/group/{group_id}/bill", response_model=GroupBillResponse,
            summary="Cuenta consolidada de un grupo de mesas (RF-053)")
def group_bill(
    group_id: UUID,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    return tables_advanced.group_bill(db, group_id)


@router.get(
    "/tables/{table_id}/qr-token",
    response_model=TableQrTokenResponse,
    summary="Emitir token firmado del QR de la mesa (imprimible)",
)
def issue_table_qr_token(
    table_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(require_tenant_admin),
):
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    token = mint_qr_token(tenant.id, table.id)
    return TableQrTokenResponse(
        table_id=table.id,
        number=table.number,
        qr_token=token,
        menu_path=f"/api/v1/menu/qr-token/{token}",
    )


# NOTA: los endpoints legacy de sesión de mesa se eliminaron.
# - `POST /sessions` (qr_token UUID plano + header x-tenant-host) → `POST /cart/sessions`,
#   que autentica con el token de QR firmado y devuelve token de sesión + carrito.
#   Además su manejo de 409 era código muerto: esperaba un IntegrityError que
#   ninguna constraint producía.
# - `POST /sessions/{id}/close` → el cierre ahora es de la sesión de **mesa**
#   completa, en el router de `table_sessions`.


# ============================ Confirmación (staff) ============================
@router.post(
    "/{order_id}/confirm",
    response_model=OrderResponse,
    summary="Confirmar un pedido recibido del QR (descuenta inventario)",
)
def confirm_order(
    order_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    """`recibida` → `abierta`. Es el único punto donde el pedido del comensal
    compromete stock; hasta aquí no había tocado inventario."""
    checkout.confirm_order(db, order_id, user)
    order = _load_order(db, order_id)
    events.order_confirmed(
        tenant.id, order_id=order.id, table_session_id=order.table_session_id
    )
    events.bill_changed(tenant.id, table_session_id=order.table_session_id)
    return order


# ============================ Consolidación (mesero) ============================
@router.post(
    "/tables/{table_id}/consolidate",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Consolidar carritos de la mesa en la orden abierta (mesero)",
)
def consolidate(
    table_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = consolidate_table(db, table_id, user)
    return _load_order(db, order.id)


@router.post(
    "/tables/{table_id}/items",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un ítem directo a la orden de la mesa (mesero)",
)
def add_table_item(
    table_id: UUID,
    body: OrderItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = add_item_to_table(db, table_id, body, user)
    return _load_order(db, order.id)


# ============================ KDS / cocina ============================
@router.get("/kds", response_model=list[KdsOrderResponse], summary="Pantalla de cocina (ítems activos por mesa/orden)")
def kds_board(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return kitchen.list_kds(db)


@router.patch(
    "/items/{item_id}/kitchen",
    response_model=OrderItemResponse,
    summary="Avanzar estado de cocina de un ítem (pendiente→en_preparacion→listo→entregado)",
)
def kitchen_transition(
    item_id: UUID, body: KitchenTransitionIn,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    """Devuelve `rt_v`: la versión del evento que acaba de emitir esta escritura.

    El KDS parchea el ítem localmente tras el PATCH (escritura optimista). Sin
    `rt_v` no tendría con qué descartar un evento en vuelo de otra pantalla que
    llegara justo después y revirtiera visualmente lo que el cocinero ya vio.
    """
    item = kitchen.transition_kitchen(db, item_id, body)
    order = db.get(CustomerOrder, item.order_id)
    entry_id = events.item_kitchen_changed(
        tenant.id,
        order_id=item.order_id,
        table_session_id=order.table_session_id if order else None,
        item_id=item.id,
        estado_cocina=item.estado_cocina,
    )
    out = OrderItemResponse.model_validate(item)
    if entry_id is not None:
        out.rt_v = events.version_of(entry_id)
    return out


@router.post(
    "/items/{item_id}/void",
    response_model=OrderResponse,
    summary="Anular (y opcionalmente reemplazar) un ítem de la orden",
)
def void_item(
    item_id: UUID, body: VoidItemIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    order = kitchen.void_item(db, item_id, body, user)
    reemplazo = next((i for i in order.items if i.void_de == item_id), None)
    events.item_voided(
        tenant.id,
        order_id=order.id,
        table_session_id=order.table_session_id,
        item_id=item_id,
        motivo=body.motivo,
        replacement_item_id=reemplazo.id if reemplazo else None,
    )
    events.bill_changed(tenant.id, table_session_id=order.table_session_id)
    return _load_order(db, order.id)


# ============================ Cobro / cierre (Fase 7) ============================
@router.get(
    "/tables/{table_id}/bill",
    response_model=BillResponse,
    summary="Cuenta de la mesa (total + split por comensal)",
)
def table_bill(table_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return checkout.compute_bill(db, table_id)


@router.post(
    "/{order_id}/block",
    response_model=OrderResponse,
    summary="Bloquear orden para cobro (lock optimista + validación de cocina)",
)
def block_order(
    order_id: UUID, body: BlockIn,
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    order = checkout.block_order(db, order_id, body)
    return _load_order(db, order.id)


@router.post(
    "/{order_id}/pay",
    response_model=SaleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cobrar orden bloqueada (crea venta, marca pagada)",
)
def pay_order(
    order_id: UUID, body: PayIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    sale = checkout.pay_order(db, order_id, body, user)
    order = db.get(CustomerOrder, order_id)
    events.payment_completed(
        tenant.id,
        sale_id=sale.id,
        table_session_id=order.table_session_id if order else None,
        total=sale.total,
        customer_name=order.customer_name if order else None,
        # Cobro pedido a pedido desde el mostrador, no cierre de sesión de mesa.
        billing_mode="counter",
    )
    return sale


@router.post(
    "/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Cancelar pedido (reversa parcial de inventario + auditoría)",
)
def cancel_order(
    order_id: UUID, body: CancelIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    """El staff puede cancelar en cualquier estado no terminal. Solo vuelve al stock
    lo que cocina no llegó a preparar; lo ya consumido se registra como pérdida."""
    order = checkout.cancel_order(db, order_id, body, user)
    events.order_cancelled(
        tenant.id, order_id=order.id, table_session_id=order.table_session_id,
        motivo=body.motivo,
    )
    events.bill_changed(tenant.id, table_session_id=order.table_session_id)
    return _load_order(db, order.id)


@router.post(
    "/tables/{table_id}/release",
    response_model=TableResponse,
    summary="Liberar mesa (regla dura: cero órdenes no-terminales)",
)
def release_table(
    table_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    table = checkout.release_table(db, table_id, closed_by=user)
    events.table_status_changed(
        tenant.id, dining_table_id=table.id, table_number=table.number, status=table.status,
    )
    return table


# ============================ Facturación ============================
# Los endpoints de generación manual se eliminaron: la factura se emite sola al
# cobrar, dentro de la transacción de la venta (`sales/builder.py:build_sale`).
#
# Además partían del **pedido**, que es la unidad equivocada: `Invoice.sale_id` es
# único —una factura por venta— y tras el cobro por sesión un cierre `split` emite
# N ventas, mientras que una venta de mostrador no cuelga de ningún pedido. Por eso
# 12 de 20 ventas reales eran imposibles de facturar por esa vía.
#
# Consulta e impresión: `GET /invoices` y `GET /invoices/{id}`.


# ============================ Comandas ============================
@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Crear comanda (staff)")
def create_order(
    body: OrderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Alta de comanda por staff (mostrador/mesero). **No es una ruta pública**:
    el pedido anónimo por QR entra por el carrito del comensal, no por aquí.

    Antes era anónima (solo header `x-tenant-host`, falsificable) y además no
    descontaba inventario, así que una comanda creada aquí y cobrada con
    `pay_order` nunca descontaba stock."""
    order = service.create_order(db, body, user_id=user.id)
    return _load_order(db, order.id)


@router.get("", response_model=list[OrderResponse], summary="Listar comandas (staff)")
def list_orders(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """La terminal y el KDS sondean esto, así que responde con `ETag`: mientras
    nada cambie el navegador revalida y recibe un 304, sin cuerpo ni re-render."""
    q = select(CustomerOrder).options(
        selectinload(CustomerOrder.items).selectinload(OrderItem.options)
    ).order_by(CustomerOrder.created_at.desc())
    if status_filter is not None:
        q = q.where(CustomerOrder.status == status_filter)
    return json_or_304(request, _ORDERS_ADAPTER, db.execute(q).scalars().all())


@router.get("/{order_id}", response_model=OrderResponse, summary="Obtener una comanda")
def get_order(order_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _load_order(db, order_id)


# `PATCH /{order_id}/status` se eliminó a propósito: asignaba cualquier estado sin
# validar la transición y sin tocar inventario, así que permitía pasar un pedido de
# 'recibida' a 'abierta' esquivando `confirm_order` —el único punto que descuenta
# stock— y dejaba el inventario sobrestimado sin que nadie se enterara.
#
# Cada transición legítima tiene su endpoint, con sus reglas:
#   recibida → abierta    POST /orders/{id}/confirm      (descuenta inventario)
#   abierta  → bloqueada  POST /orders/{id}/block        (valida cocina)
#   → pagada              POST /table-sessions/{id}/close (cobra y libera la mesa)
#   → cancelada           POST /orders/{id}/cancel       (revierte lo no preparado)
