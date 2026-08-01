"""Ciclo de vida de la sesión de mesa: consulta, cuenta consolidada y cierre con
elección de `billing_mode`.

El cierre es el punto donde la mesa se cobra y se libera. `unified` emite una sola
venta con todo lo consumido; `split` emite una venta por comensal, agrupando por
`order_items.participant_id` — que es asignación **por ítem**, así que el reparto
es exacto aunque un pedido mezcle comensales.
"""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import events
from app.core.crud import get_or_404
from app.core.models import User
from app.models.customer_order import CustomerOrder
from app.models.dining_table import DiningTable
from app.models.order_item import OrderItem
from app.models.sale import Sale
from app.models.session_participant import SessionParticipant
from app.models.table_session import TableSession
from app.api.v1.orders import checkout
from app.api.v1.sales.builder import build_sale, ensure_open_shift
from app.api.v1.table_sessions.schemas import (
    BillingMode, CloseSessionIn, CloseSessionResponse,
    SessionBillLine, SessionBillResponse, TableSessionResponse,
)

logger = logging.getLogger(__name__)

# Estados de cocina que impiden cerrar: hay comida sin entregar.
_EN_CURSO = ("pendiente", "en_preparacion")


def _load(db: Session, table_session_id: UUID) -> TableSession:
    ts = db.execute(
        select(TableSession)
        .options(selectinload(TableSession.participants))
        .where(TableSession.id == table_session_id)
    ).scalar_one_or_none()
    if ts is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sesión de mesa no encontrada")
    return ts


def get_session(db: Session, table_session_id: UUID) -> TableSession:
    return _load(db, table_session_id)


# ------------------------------------------------------ Liberar mesa abandonada

def has_billable_orders(db: Session, table_session_id: UUID) -> bool:
    """¿Queda algún pedido que cobrar en la sesión? `pagada`/`cancelada` no cuentan."""
    return db.execute(
        select(CustomerOrder.id).where(
            CustomerOrder.table_session_id == table_session_id,
            CustomerOrder.status.notin_(checkout.TERMINAL),
        ).limit(1)
    ).scalar() is not None


def try_release_if_empty(db: Session, table_session_id: UUID) -> bool:
    """Cierra la sesión y devuelve la mesa a `libre` **solo si** ya no queda nadie
    y no hay nada que cobrar. Devuelve si la liberó.

    Es el único punto que decide esto; lo llaman el "salir" del comensal, la
    expiración de su token, la cancelación de su último pedido y el barrido.

    Sin commit: se une a la transacción del caller.

    Dos condiciones, ambas necesarias:

    - **Nadie activo.** Si queda un comensal `open`, la mesa sigue ocupada aunque
      quien se fue no hubiera pedido nada.
    - **Nada que cobrar.** Si hay pedidos vivos la mesa **no** se libera aunque no
      quede nadie: es un descuadre real que debe ver el personal, no algo que
      convenga tapar automáticamente.
    """
    ts = db.get(TableSession, table_session_id)
    if ts is None or ts.status != "active":
        return False

    # La sesión se abre con `autoflush=False` (ver `with_db`), así que sin este
    # flush las consultas de abajo no verían el comensal que el caller acaba de
    # cerrar en memoria: la mesa nunca se liberaría y el endpoint respondería OK.
    db.flush()

    quedan = db.execute(
        select(SessionParticipant.id).where(
            SessionParticipant.table_session_id == ts.id,
            SessionParticipant.status == "open",
        ).limit(1)
    ).scalar()
    if quedan is not None:
        return False

    if has_billable_orders(db, ts.id):
        return False

    checkout.close_table_sessions(db, ts.dining_table_id, closed_by=None)
    table = db.get(DiningTable, ts.dining_table_id)
    if table is not None:
        table.status = "libre"
    return True


def list_sessions(db: Session, *, only_active: bool = True) -> list[TableSession]:
    stmt = select(TableSession).options(selectinload(TableSession.participants))
    if only_active:
        stmt = stmt.where(TableSession.status == "active")
    return db.execute(stmt.order_by(TableSession.opened_at.desc())).scalars().all()


def _billable_orders(db: Session, table_session_id: UUID) -> list[CustomerOrder]:
    """Pedidos de la sesión que entran en la cuenta: ni cancelados ni ya pagados."""
    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.table_session_id == table_session_id,
            CustomerOrder.status.notin_(("cancelada", "pagada")),
        )
        .order_by(CustomerOrder.created_at)
    ).scalars().all()


def compute_bill(db: Session, table_session_id: UUID) -> SessionBillResponse:
    ts = _load(db, table_session_id)
    orders = _billable_orders(db, ts.id)

    labels = {p.id: (p.display_label or p.display_name) for p in ts.participants}

    total = Decimal("0")
    split: dict[UUID | None, Decimal] = {}
    for order in orders:
        for it in order.items:
            if it.estado_cocina == "anulado":
                continue
            line_total = Decimal(it.unit_price) * it.quantity
            total += line_total
            split[it.participant_id] = split.get(it.participant_id, Decimal("0")) + line_total

    return SessionBillResponse(
        table_session_id=ts.id,
        dining_table_id=ts.dining_table_id,
        total=total,
        order_ids=[o.id for o in orders],
        split=[
            SessionBillLine(
                participant_id=pid,
                display_label=labels.get(pid) if pid else None,
                subtotal=monto,
            )
            for pid, monto in split.items()
        ],
    )


def _assert_closable(db: Session, orders: list[CustomerOrder]) -> None:
    """Una sesión no se cierra con pedidos sin confirmar ni con comida en curso."""
    sin_confirmar = [o for o in orders if o.status == "recibida"]
    if sin_confirmar:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "Hay pedidos recibidos sin confirmar; confírmalos o cancélalos.",
                "order_ids": [str(o.id) for o in sin_confirmar],
            },
        )

    en_cocina = [
        {"order_id": str(o.id), "order_item_id": str(it.id),
         "estado_cocina": it.estado_cocina}
        for o in orders for it in o.items
        if it.estado_cocina in _EN_CURSO
    ]
    if en_cocina:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "Hay ítems sin terminar en cocina; anúlalos o espera a que estén listos.",
                "items": en_cocina,
            },
        )


def close_session(
    db: Session, table_session_id: UUID, data: CloseSessionIn, cashier: User,
    *, invoice_prefix: str = "", tenant_id: int | None = None,
) -> CloseSessionResponse:
    """Cobra y cierra la sesión de mesa. Solo staff (el comensal anónimo no puede).

    Todo en una transacción: las ventas, el paso de los pedidos a `pagada`, el
    cierre de la sesión y sus comensales, y la liberación de la mesa. Si un pago
    no cubre su parte, no se cierra nada.

    **No escribe movimientos de caja.** `cash_movements` es solo para
    ingresos/egresos manuales; las ventas del turno las deriva `reconcile` desde
    `Payment`. Insertar además un movimiento contaría el dinero dos veces.
    """
    ts = _load(db, table_session_id)
    if ts.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"La sesión ya está {ts.status}"
        )

    shift = ensure_open_shift(db, data.cash_shift_id)
    orders = _billable_orders(db, ts.id)
    if not orders:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La sesión no tiene pedidos que cobrar"
        )
    _assert_closable(db, orders)

    try:
        if data.billing_mode is BillingMode.UNIFIED:
            sales = [_close_unified(db, ts, orders, data, shift, cashier, invoice_prefix)]
        else:
            sales = _close_split(db, ts, orders, data, shift, cashier, invoice_prefix)

        for order in orders:
            order.status = "pagada"

        ts.billing_mode = data.billing_mode.value
        checkout.close_table_sessions(db, ts.dining_table_id, closed_by=cashier)

        table = db.get(DiningTable, ts.dining_table_id)
        if table is not None:
            table.status = "libre"

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cerrando la sesión de mesa")
        raise

    # Publica el servicio y no el router porque la cascada necesita los objetos
    # `Sale` con su factura, que solo existen dentro de esta función. Siempre
    # después del COMMIT: es un único commit para toda la cascada, así que hasta
    # aquí nada de esto era definitivo.
    if tenant_id is not None:
        for sale in sales:
            events.payment_completed(
                tenant_id,
                sale_id=sale.id,
                table_session_id=ts.id,
                total=sale.total,
                customer_name=getattr(sale, "customer_name", None),
                billing_mode=data.billing_mode.value,
                invoice=(
                    {"prefix": sale.invoice.prefix, "number": sale.invoice.number}
                    if getattr(sale, "invoice", None) else None
                ),
            )
        events.session_closed(
            tenant_id,
            table_session_id=ts.id,
            dining_table_id=ts.dining_table_id,
            reason="paid",
        )
        if table is not None:
            events.table_status_changed(
                tenant_id, dining_table_id=table.id, table_number=table.number,
                status="libre",
            )

    return CloseSessionResponse(
        table_session=TableSessionResponse.model_validate(_load(db, table_session_id)),
        sale_ids=[s.id for s in sales],
    )


def _participantes_con_consumo(orders: list[CustomerOrder]) -> set:
    """Comensales con algo que cobrar. `None` agrupa lo que añadió el mesero."""
    return {
        it.participant_id
        for o in orders for it in o.items
        if it.estado_cocina != "anulado"
    }


def _nombre_cuenta(
    db: Session, ts: TableSession, orders: list[CustomerOrder], data: CloseSessionIn
) -> str | None:
    """A nombre de quién se emite la factura de una cuenta unificada.

    Manda lo que escriba el cajero —puede ser una empresa que pide la factura a
    su nombre—; si no escribe nada se usan los comensales de la sesión, y si
    tampoco hay (mesa atendida solo por el mesero) la propia mesa. Una factura
    sin nombre no le sirve a nadie."""
    escrito = (data.customer_name or "").strip()
    if escrito:
        return escrito[:255]

    con_consumo = _participantes_con_consumo(orders)
    nombres = [
        p.display_label or p.display_name
        for p in ts.participants
        if p.id in con_consumo
    ]
    if nombres:
        return ", ".join(sorted(nombres))[:255]

    table = db.get(DiningTable, ts.dining_table_id)
    return f"Mesa {table.number}" if table is not None else None


def _close_unified(
    db: Session, ts: TableSession, orders: list[CustomerOrder],
    data: CloseSessionIn, shift, cashier: User, invoice_prefix: str = "",
) -> Sale:
    """Una sola venta con las líneas de todos los pedidos de la sesión."""
    if not data.payments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "billing_mode='unified' requiere 'payments'",
        )

    lines = []
    for order in orders:
        lines.extend(checkout.order_sale_lines(db, order.id))

    return build_sale(
        db,
        lines=lines,
        shift=shift,
        cashier=cashier,
        payments=data.payments,
        discount=data.discount, tax=data.tax, tip=data.tip,
        customer_name=_nombre_cuenta(db, ts, orders, data),
        dining_table_id=ts.dining_table_id,
        table_session_id=ts.id,
        # Una venta unificada cubre a varios comensales: no cuelga de ninguno.
        customer_order_id=orders[0].id if len(orders) == 1 else None,
        invoice_prefix=invoice_prefix,
    )


def _close_split(
    db: Session, ts: TableSession, orders: list[CustomerOrder],
    data: CloseSessionIn, shift, cashier: User, invoice_prefix: str = "",
) -> list[Sale]:
    """Una venta por comensal. Se exige un bloque de pago por cada comensal con
    consumo: si falta uno, la mesa quedaría medio cobrada."""
    if not data.splits:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "billing_mode='split' requiere 'splits'",
        )

    con_consumo = _participantes_con_consumo(orders)
    cubiertos = {s.participant_id for s in data.splits}
    faltan = con_consumo - cubiertos
    if faltan:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Faltan comensales por cobrar en el split.",
                "participant_ids": [str(p) if p else None for p in faltan],
            },
        )
    sobran = cubiertos - con_consumo
    if sobran:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "El split incluye comensales sin consumo.",
                "participant_ids": [str(p) if p else None for p in sobran],
            },
        )

    labels = {p.id: (p.display_label or p.display_name) for p in ts.participants}

    sales: list[Sale] = []
    for bloque in data.splits:
        lines = []
        for order in orders:
            lines.extend(checkout.order_sale_lines(
                db, order.id, participant_id=bloque.participant_id
            ))
        sales.append(build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=bloque.payments,
            discount=bloque.discount, tax=bloque.tax, tip=bloque.tip,
            customer_name=labels.get(bloque.participant_id),
            dining_table_id=ts.dining_table_id,
            table_session_id=ts.id,
            participant_id=bloque.participant_id,
            invoice_prefix=invoice_prefix,
        ))
    return sales
