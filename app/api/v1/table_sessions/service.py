"""Ciclo de vida de la sesión de mesa: consulta, cuenta consolidada y cierre con
elección de `billing_mode`.

El cierre es el punto donde la mesa se cobra y se libera. `unified` emite una sola
venta con todo lo consumido; `split` emite una venta por comensal, agrupando por
`order_items.participant_id` — que es asignación **por ítem**, así que el reparto
es exacto aunque un pedido mezcle comensales.
"""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core import events
from app.core.crud import get_or_404
from app.core.models import User
from app.models.customer_order import CustomerOrder
from app.models.dining_table import DiningTable
from app.models.order_item import OrderItem, OrderItemOption
from app.models.sale import Sale
from app.models.session_participant import SessionParticipant
from app.models.table_session import TableSession
from app.api.v1.orders import checkout
from app.api.v1.sales.builder import build_sale, ensure_open_shift
from app.api.v1.promotions import service as promotions
from app.api.v1.table_sessions.schemas import (
    BillingMode, CloseSessionIn, CloseSessionResponse,
    SessionBillLine, SessionBillResponse, TableSessionResponse,
)

logger = logging.getLogger(__name__)

# Estados de cocina que impiden cerrar: hay comida sin entregar.
_EN_CURSO = ("pendiente", "en_preparacion")


def _load(db: Session, table_session_id: UUID, *, lock: bool = False) -> TableSession:
    """Carga la sesión. Con `lock=True` toma `FOR UPDATE` sobre la fila.

    El lock es obligatorio en el cierre: sin él, dos `POST /close` concurrentes leen
    ambos `status == 'active'` y cobran la mesa dos veces. El check de estado no vale
    de nada si se lee sin bloquear.
    """
    stmt = select(TableSession).where(TableSession.id == table_session_id)
    if lock:
        # `selectinload` emite una segunda consulta y no se puede combinar con
        # FOR UPDATE en la misma; los participantes se cargan luego, ya con el lock.
        stmt = stmt.with_for_update()
    else:
        stmt = stmt.options(selectinload(TableSession.participants))
    ts = db.execute(stmt).scalar_one_or_none()
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
    """Cuenta de la sesión, ya con descuentos automáticos (promociones/combos)
    aplicados — mismo cálculo por comensal que usa `_close_split` al cobrar, para
    que este preview coincida con lo que realmente se cobrará."""
    ts = _load(db, table_session_id)
    orders = _billable_orders(db, ts.id)

    labels = {p.id: (p.display_label or p.display_name) for p in ts.participants}

    participant_ids: list[UUID | None] = []
    seen: set[UUID | None] = set()
    for order in orders:
        for it in order.items:
            if it.estado_cocina == "anulado" or it.participant_id in seen:
                continue
            seen.add(it.participant_id)
            participant_ids.append(it.participant_id)

    now = datetime.now(timezone.utc)
    total = Decimal("0")
    split: list[SessionBillLine] = []
    for pid in participant_ids:
        lines = []
        for order in orders:
            lines.extend(checkout.order_sale_lines(db, order.id, participant_id=pid))
        raw_subtotal = sum((line.line_total for line in lines), Decimal("0"))
        promo_discount, _ = promotions.evaluate(db, checkout.promo_lines_for(db, lines), now)
        combo_discount = promotions.combo_discount_for_lines(db, lines, now)
        subtotal = raw_subtotal - promo_discount - combo_discount
        total += subtotal
        split.append(SessionBillLine(
            participant_id=pid,
            display_label=labels.get(pid) if pid else None,
            subtotal=subtotal,
        ))

    return SessionBillResponse(
        table_session_id=ts.id,
        dining_table_id=ts.dining_table_id,
        total=total,
        order_ids=[o.id for o in orders],
        split=split,
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
    # Con lock: el check de estado que sigue solo sirve si nadie más puede estar
    # cobrando esta misma sesión a la vez.
    ts = _load(db, table_session_id, lock=True)
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


# -------------------------------------------- Reparto de la cuenta (staff)

def _unique_label(db: Session, table_session_id: UUID, nombre: str) -> str:
    """Import local: `cart.service` ya importa de este módulo, así que a nivel de
    módulo sería un ciclo (mismo motivo que `issue_for_sale` en `sales/builder.py`)."""
    from app.api.v1.cart.service import unique_display_label

    return unique_display_label(db, table_session_id, nombre)


def add_participant(
    db: Session, table_session_id: UUID, display_name: str,
    *, tenant_id: int | None = None,
) -> SessionParticipant:
    """Crea un comensal desde el POS, sin QR.

    Es lo que permite dividir la cuenta cuando una sola persona pidió por todos: el
    `participant_id` de los ítems solo se poblaba al escanear, así que sin esto el
    desglose tenía una única línea y el cobro dividido era inalcanzable.

    Sin `expires_at` y sin carrito **a propósito**: no hay token que emitir, así que
    nadie puede entrar por el QR como esta persona. Es una etiqueta de cobro.
    """
    ts = _load(db, table_session_id)
    if ts.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La sesión ya está {ts.status}: no se pueden agregar comensales.",
        )

    nombre = display_name.strip()
    if not nombre:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre no puede estar vacío")

    participant = SessionParticipant(
        table_session_id=ts.id,
        dining_table_id=ts.dining_table_id,
        display_name=nombre,
        # Reutiliza el desambiguador del QR: dos "Ana" en la misma mesa quedan
        # "Ana" y "Ana (2)", vengan de donde vengan.
        display_label=_unique_label(db, ts.id, nombre),
        status="open",
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)
    _notify_bill_changed(ts, tenant_id)
    return participant


def remove_participant(
    db: Session, table_session_id: UUID, participant_id: UUID,
    *, tenant_id: int | None = None,
) -> None:
    """Quita un comensal de la sesión.

    Se niega si tiene consumo: borrarlo dejaría sus líneas apuntando a una fila que ya
    no existe y el cobro fallaría al armar el desglose. Primero hay que reasignar."""
    ts = _load(db, table_session_id)
    participant = db.get(SessionParticipant, participant_id)
    if participant is None or participant.table_session_id != ts.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Comensal no encontrado en esta sesión")

    asignados = db.execute(
        select(func.count(OrderItem.id)).where(OrderItem.participant_id == participant_id)
    ).scalar_one()
    if asignados:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    f"«{participant.display_label or participant.display_name}» tiene "
                    f"{asignados} producto(s) asignado(s). Reasígnalos antes de quitarlo."
                ),
                "items": asignados,
            },
        )

    db.delete(participant)
    db.commit()
    _notify_bill_changed(ts, tenant_id)


def set_assignments(
    db: Session, table_session_id: UUID, assignments: list,
    *, tenant_id: int | None = None,
) -> SessionBillResponse:
    """Reparte líneas entre comensales, en una sola transacción.

    Devuelve la cuenta ya recalculada para que el POS no tenga que pedirla aparte.
    """
    ts = _load(db, table_session_id)
    if ts.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La sesión ya está {ts.status}: la cuenta no se puede repartir.",
        )

    validos = {p.id for p in ts.participants}
    # Los ítems cobrables de esta sesión: todo lo demás se rechaza por id, así se
    # evita repartir líneas de otra mesa o de un pedido ya cobrado.
    items = {
        it.id: it
        for it in db.execute(
            select(OrderItem)
            .join(CustomerOrder, CustomerOrder.id == OrderItem.order_id)
            .where(
                CustomerOrder.table_session_id == ts.id,
                CustomerOrder.status.notin_(("cancelada", "pagada")),
            )
        ).scalars()
    }

    # Una línea de 2 unidades repartida entre dos personas llega como DOS entradas del
    # mismo ítem, así que hay que verlas juntas para poder validar y partir.
    por_item: dict[UUID, list] = {}
    for a in assignments:
        item = items.get(a.order_item_id)
        if item is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El producto {a.order_item_id} no pertenece a la cuenta de esta mesa.",
            )
        if item.estado_cocina == "anulado":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Un producto anulado no se cobra, así que no se puede asignar.",
            )
        if a.participant_id is not None and a.participant_id not in validos:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El comensal {a.participant_id} no es de esta mesa.",
            )
        por_item.setdefault(a.order_item_id, []).append(a)

    for item_id, entradas in por_item.items():
        item = items[item_id]
        pedidas = [(e.quantity if e.quantity is not None else item.quantity) for e in entradas]

        # **La invariante que hace segura esta operación**: partir 2 en 1+1 no cambia
        # lo consumido, y el inventario se descontó al confirmar según esa cantidad.
        # Una suma de más inventaría unidades que nadie pidió; una de menos las haría
        # desaparecer de la cuenta. Ninguna puede pasar en silencio.
        if sum(pedidas) != item.quantity:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El reparto de este producto suma {sum(pedidas)} unidad(es) y la "
                f"línea tiene {item.quantity}.",
            )

        if len(entradas) == 1:
            item.participant_id = entradas[0].participant_id
            continue

        # Partir en cocina duplicaría el ticket del KDS con la comida a medio hacer.
        # No estorba: cobrar ya exige que cocina haya terminado.
        if item.estado_cocina in _EN_CURSO:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "No se puede repartir por unidades un producto que cocina aún no ha "
                "terminado. Espera a que esté listo.",
            )

        # La primera entrada se queda en la fila original; las demás nacen como filas
        # nuevas copiando TODO lo que define la línea (precio, sabores, notas, estado):
        # sin las opciones, media comanda perdería su sabor.
        item.participant_id = entradas[0].participant_id
        item.quantity = pedidas[0]
        for entrada, cantidad in zip(entradas[1:], pedidas[1:]):
            nueva = OrderItem(
                order_id=item.order_id,
                participant_id=entrada.participant_id,
                product_variant_id=item.product_variant_id,
                quantity=cantidad,
                unit_price=item.unit_price,
                estado_cocina=item.estado_cocina,
                void_de=item.void_de,
                notes=item.notes,
            )
            db.add(nueva)
            db.flush()
            for opcion in item.options:
                db.add(OrderItemOption(order_item_id=nueva.id, option_id=opcion.option_id))

    db.commit()
    _notify_bill_changed(ts, tenant_id)
    return compute_bill(db, ts.id)


def _notify_bill_changed(ts: TableSession, tenant_id: int | None) -> None:
    """Avisa al POS de que el desglose cambió. Best-effort: el reparto ya está
    guardado, y no publicar el evento solo significa que el cajero verá el cambio al
    refrescar."""
    if tenant_id is None:
        return
    try:
        events.bill_changed(tenant_id, table_session_id=ts.id)
    except Exception:
        logger.warning("No se pudo publicar bill_changed de %s", ts.id, exc_info=True)


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

    now = datetime.now(timezone.utc)
    promo_discount, promo_id = promotions.evaluate(db, checkout.promo_lines_for(db, lines), now)
    combo_discount = promotions.combo_discount_for_lines(db, lines, now)
    combo_ids_used = {line.combo_id for line in lines if line.combo_id is not None}
    final_promotion_id = next(iter(combo_ids_used)) if len(combo_ids_used) == 1 else promo_id

    return build_sale(
        db,
        lines=lines,
        shift=shift,
        cashier=cashier,
        payments=data.payments,
        discount=Decimal(data.discount) + promo_discount + combo_discount,
        tax=data.tax, tip=data.tip,
        customer_name=_nombre_cuenta(db, ts, orders, data),
        dining_table_id=ts.dining_table_id,
        table_session_id=ts.id,
        # Una venta unificada cubre a varios comensales: no cuelga de ninguno.
        customer_order_id=orders[0].id if len(orders) == 1 else None,
        promotion_id=final_promotion_id,
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

    # En split cada bloque lleva sus propios importes. Los de raíz se ignoraban en
    # silencio: quien mandaba una propina ahí la perdía sin enterarse.
    if data.discount or data.tax or data.tip or data.payments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "En billing_mode='split', el descuento, impuesto, propina y pagos van "
            "dentro de cada bloque de 'splits', no en la raíz.",
        )

    # Un comensal repetido pasaría los dos chequeos de cobertura (que trabajan con
    # conjuntos) y luego el bucle cobraría SUS MISMAS LÍNEAS dos veces: dos ventas,
    # dos facturas y dos consecutivos por el mismo consumo. Se corta aquí.
    vistos: list[UUID | None] = [s.participant_id for s in data.splits]
    if len(set(vistos)) != len(vistos):
        repetidos = {p for p in vistos if vistos.count(p) > 1}
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Hay comensales repetidos en el split: cada uno paga una sola vez.",
                "participant_ids": [str(p) if p else None for p in repetidos],
            },
        )

    con_consumo = _participantes_con_consumo(orders)
    cubiertos = set(vistos)
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
    table = db.get(DiningTable, ts.dining_table_id)
    # El bloque sin comensal (lo que tecleó el mesero) no tiene etiqueta, y sin esto
    # su venta y su factura salían literalmente sin nombre. `unified` ya evitaba eso
    # con `_nombre_cuenta`; aquí faltaba el mismo cuidado.
    sin_asignar = f"Mesa {table.number}" if table is not None else "Sin asignar"

    now = datetime.now(timezone.utc)
    sales: list[Sale] = []
    for bloque in data.splits:
        lines = []
        for order in orders:
            lines.extend(checkout.order_sale_lines(
                db, order.id, participant_id=bloque.participant_id
            ))

        # Cada comensal evalúa promociones/combos sobre sus propias líneas: un
        # combo agregado por un comensal no se mezcla con las de otro.
        promo_discount, promo_id = promotions.evaluate(db, checkout.promo_lines_for(db, lines), now)
        combo_discount = promotions.combo_discount_for_lines(db, lines, now)
        combo_ids_used = {line.combo_id for line in lines if line.combo_id is not None}
        final_promotion_id = next(iter(combo_ids_used)) if len(combo_ids_used) == 1 else promo_id

        sales.append(build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=bloque.payments,
            discount=bloque.discount, tax=bloque.tax, tip=bloque.tip,
            customer_name=labels.get(bloque.participant_id) or sin_asignar,
            dining_table_id=ts.dining_table_id,
            table_session_id=ts.id,
            participant_id=bloque.participant_id,
            promotion_id=final_promotion_id,
            invoice_prefix=invoice_prefix,
        ))
    return sales
