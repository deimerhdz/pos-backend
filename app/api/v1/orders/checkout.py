"""Cobro y ciclo de cierre de una orden de mesa (Fase 7): bloqueo con lock
optimista + validación de cocina, cuenta/split por comensal, pago (crea Sale sin
re-descontar), cancelación con reversa **parcial**, y liberación de mesa.

La reversa de inventario al cancelar es asimétrica y depende del estado de cocina
de cada ítem: solo vuelve al stock lo que cocina no llegó a preparar. Ver
`cancel_order`."""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_audit
from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.table_session import TableSession
from app.models.session_participant import SessionParticipant
from app.models.cart import Cart
from app.models.option import Option
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.cash_shift import CashShift
from app.models.sale import Sale
from app.models.customer_order import CustomerOrder
from app.models.order_item import EN_CURSO, OrderItem
from app.models.order_cancel_log import OrderCancelLog
from app.api.v1.sales.builder import SaleLine, build_sale, ensure_open_shift
from app.api.v1.orders.consumption import deduct_order_items, reverse_order_items
from app.api.v1.orders.schemas import (
    BlockIn, CancelIn, PayIn,
    BillResponse, BillOrderLine, BillItemLine, BillSessionLine,
)
from app.api.v1.promotions import service as promotions

logger = logging.getLogger(__name__)

TERMINAL = ("pagada", "cancelada")

# Estados en los que el insumo YA se combinó físicamente: cancelar no lo
# devuelve al stock, es pérdida. El 'out' de la confirmación ya representa esa
# pérdida, así que tampoco se escribe un movimiento extra (sería doble descuento).
_CONSUMED_KITCHEN = ("en_preparacion", "listo")

# Estados de orden en los que el inventario todavía NO se descontó: el descuento
# ocurre al confirmar. Cancelar desde aquí no genera ningún movimiento.
_NOT_DEDUCTED = ("recibida",)


def _item_options(db: Session, item: OrderItem) -> list[Option]:
    opt_ids = [o.option_id for o in item.options]
    if not opt_ids:
        return []
    return db.execute(select(Option).where(Option.id.in_(opt_ids))).scalars().all()


def _reload_order(db: Session, order_id: UUID) -> CustomerOrder:
    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one()


# --------------------------------------------------------------------- Bloqueo

def block_order(db: Session, order_id: UUID, data: BlockIn) -> CustomerOrder:
    try:
        order = db.execute(
            select(CustomerOrder).where(CustomerOrder.id == order_id).with_for_update()
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        if order.status != "abierta":
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"La orden no está abierta (status={order.status})"
            )
        if order.version != data.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"error": "Conflicto de versión (la orden cambió)", "version_actual": order.version},
            )

        pendientes = db.execute(
            select(OrderItem).where(
                OrderItem.order_id == order.id,
                OrderItem.estado_cocina.in_(EN_CURSO),
            )
        ).scalars().all()
        if pendientes:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "error": "Hay ítems sin terminar en cocina; anúlalos o espera a que estén listos.",
                    "items": [
                        {
                            "order_item_id": str(it.id),
                            "product_variant_id": str(it.product_variant_id),
                            "estado_cocina": it.estado_cocina,
                            "participant_id": str(it.participant_id) if it.participant_id else None,
                        }
                        for it in pendientes
                    ],
                },
            )

        order.status = "bloqueada"
        order.version += 1
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error bloqueando orden para cobro")
        raise

    return _reload_order(db, order_id)


# ----------------------------------------------------------------- Cuenta/split

def compute_bill(db: Session, table_id: UUID) -> BillResponse:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    orders = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status != "cancelada",
        )
        .order_by(CustomerOrder.created_at)
    ).scalars().all()

    # nombres de comensal por sesión
    names = dict(db.execute(
        select(SessionParticipant.id, SessionParticipant.display_label)
    ).all())

    total = Decimal("0")
    order_lines: list[BillOrderLine] = []
    split: dict[UUID | None, Decimal] = {}

    for order in orders:
        subtotal = Decimal("0")
        items: list[BillItemLine] = []
        for it in order.items:
            if it.estado_cocina == "anulado":
                continue
            line_total = Decimal(it.unit_price) * it.quantity
            subtotal += line_total
            split[it.participant_id] = split.get(it.participant_id, Decimal("0")) + line_total
            items.append(BillItemLine(
                order_item_id=it.id, product_variant_id=it.product_variant_id,
                participant_id=it.participant_id, quantity=it.quantity,
                unit_price=it.unit_price, line_total=line_total,
                estado_cocina=it.estado_cocina,
            ))
        total += subtotal
        order_lines.append(BillOrderLine(
            order_id=order.id, status=order.status, subtotal=subtotal, items=items,
        ))

    split_lines = [
        BillSessionLine(
            participant_id=sid,
            display_label=names.get(sid) if sid else None,
            subtotal=amount,
        )
        for sid, amount in split.items()
    ]

    return BillResponse(
        dining_table_id=table.id, total=total, orders=order_lines, split=split_lines,
    )


# ------------------------------------------------------------------------ Pago

#: Centinela para "sin filtrar por comensal". No sirve `None`: en el cobro split,
#: `participant_id=None` significa justamente "las líneas sin comensal asignado"
#: (las que metió el mesero), que es un filtro `IS NULL`, no la ausencia de filtro.
ALL_PARTICIPANTS = object()


def order_sale_lines(
    db: Session, order_id: UUID, *, participant_id=ALL_PARTICIPANTS
) -> list[SaleLine]:
    """Líneas cobrables de un pedido, convertidas al formato del constructor de
    venta (con el snapshot inmutable de descripción y opciones).

    Con `participant_id` devuelve solo las de ese comensal (o las sin asignar si
    se pasa `None`): es lo que hace posible el cobro `split`, una venta por
    persona."""
    stmt = (
        select(OrderItem)
        .options(selectinload(OrderItem.options))
        .where(OrderItem.order_id == order_id, OrderItem.estado_cocina != "anulado")
    )
    if participant_id is not ALL_PARTICIPANTS:
        stmt = stmt.where(OrderItem.participant_id == participant_id)

    lines: list[SaleLine] = []
    for it in db.execute(stmt).scalars():
        variant = db.get(ProductVariant, it.product_variant_id)
        product = db.get(Product, variant.product_id) if variant else None
        description = (
            f"{product.name} - {variant.name}" if product
            else (variant.name if variant else "")
        )
        lines.append(SaleLine(
            product_variant_id=it.product_variant_id,
            description=description,
            options=[
                {"option_id": str(o.id), "name": o.name,
                 "extra_price": str(o.extra_price)}
                for o in _item_options(db, it)
            ],
            quantity=it.quantity,
            unit_price=Decimal(it.unit_price),
            combo_id=it.combo_id,
        ))
    return lines


def promo_lines_for(db: Session, lines: list[SaleLine]) -> list[dict]:
    """`promo_lines` (product_id/category_id/quantity/line_total) para las
    líneas que NO vienen de un combo — las de combo ya tienen su propio ahorro
    vía `combo_discount_for_lines` y no se acumulan con percent/fixed."""
    promo_lines: list[dict] = []
    for line in lines:
        if line.combo_id is not None:
            continue
        variant = db.get(ProductVariant, line.product_variant_id)
        product = db.get(Product, variant.product_id) if variant else None
        promo_lines.append({
            "product_id": variant.product_id if variant else None,
            "category_id": product.category_id if product else None,
            "quantity": line.quantity,
            "line_total": line.line_total,
        })
    return promo_lines


def pay_order(db: Session, order_id: UUID, data: PayIn, cashier: User) -> Sale:
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status != "bloqueada":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La orden debe estar bloqueada para cobrar (bloquea primero).",
        )
    shift = ensure_open_shift(db, data.cash_shift_id)

    try:
        now = datetime.now(timezone.utc)
        lines = order_sale_lines(db, order.id)

        # Descuento automático (RF-012): percent/fixed sobre las líneas sin
        # combo, más el ahorro de los combos presentes. Antes esta orden no
        # aplicaba ninguna promoción; ahora usa el mismo motor que mostrador.
        promo_discount, promo_id = promotions.evaluate(db, promo_lines_for(db, lines), now)
        combo_discount = promotions.combo_discount_for_lines(db, lines, now)
        combo_ids_used = {line.combo_id for line in lines if line.combo_id is not None}
        final_promotion_id = next(iter(combo_ids_used)) if len(combo_ids_used) == 1 else promo_id

        sale = build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=data.payments,
            discount=Decimal(data.discount) + promo_discount + combo_discount,
            tax=data.tax, tip=data.tip,
            customer_name=order.customer_name,
            dining_table_id=order.dining_table_id,
            table_session_id=order.table_session_id,
            participant_id=order.participant_id,
            customer_order_id=order.id,
            promotion_id=final_promotion_id,
        )
        order.status = "pagada"
        # NO se descuenta inventario aquí: ya se hizo al confirmar el pedido.

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cobrando la orden de mesa")
        raise

    return db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .where(Sale.id == sale.id)
    ).scalar_one()


# --------------------------------------------------------------- Confirmación

def confirm_order(db: Session, order_id: UUID, user: User) -> CustomerOrder:
    """`recibida` → `abierta`: el staff acepta el pedido que envió el comensal y
    **aquí es donde se compromete el inventario**.

    Es el único punto de descuento de los pedidos por QR. La validación de stock es
    la real (con lock por fila, en orden canónico de id para no deadlockear); el
    chequeo del carrito era solo preventivo y pudo quedar obsoleto. Si falta stock
    de un solo insumo, la transacción entera hace rollback y el pedido sigue
    `recibida`, listo para reintentar o cancelar."""
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
        .with_for_update(of=CustomerOrder)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status != "recibida":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Solo se confirman pedidos en 'recibida' (status={order.status})",
        )

    try:
        entries = [
            (it, _item_options(db, it))
            for it in order.items
            if it.estado_cocina != "anulado"
        ]
        if not entries:
            raise HTTPException(status.HTTP_409_CONFLICT, "El pedido no tiene ítems")

        deduct_order_items(db, entries, user.id, reference_id=order.id)

        order.status = "abierta"
        order.version += 1
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error confirmando el pedido")
        raise

    return _reload_order(db, order_id)


# ------------------------------------------------------------------ Cancelación

def cancel_order(
    db: Session,
    order_id: UUID,
    data: CancelIn,
    user: User | None,
    participant: SessionParticipant | None = None,
) -> CustomerOrder:
    """Cancela un pedido y ajusta el inventario **según lo que se alcanzó a
    consumir**, no como una reversa simétrica:

    - orden aún sin confirmar (`recibida`): nunca se descontó → cero movimientos;
    - ítem `pendiente`: descontado pero no preparado → entrada real ('in');
    - ítem `en_preparacion`/`listo`: el insumo ya se combinó → **no vuelve al
      stock**. El 'out' de la confirmación ya es esa pérdida; escribir otro
      movimiento la descontaría dos veces. Se traza en `audit_logs`;
    - ítem `anulado`: ya lo resolvió `void_item`.

    Devolver todo al stock (comportamiento anterior) sobrestimaba el inventario en
    silencio hasta el conteo físico.

    El actor es `user` (staff) **o** `participant` (el propio comensal desde el QR,
    que no es un usuario del sistema). Quién puede cancelar en qué estado lo decide
    quien llama: aquí no hay restricción de estado más allá de los terminales,
    porque el staff sí puede cancelar en cualquier momento.
    """
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status in TERMINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"La orden ya es terminal (status={order.status})"
        )

    deducted = order.status not in _NOT_DEDUCTED

    try:
        a_revertir: list[tuple[OrderItem, list[Option]]] = []
        perdidos: list[dict] = []

        for it in order.items:
            if it.estado_cocina == "anulado":
                # Ya se resolvió al anular el ítem (void_item revirtió si procedía).
                continue
            if not deducted:
                # La orden nunca llegó a descontar stock: no hay nada que revertir.
                continue
            if it.estado_cocina in _CONSUMED_KITCHEN:
                # Insumo ya consumido: no vuelve a stock. Se registra como pérdida.
                perdidos.append({
                    "order_item_id": str(it.id),
                    "product_variant_id": str(it.product_variant_id),
                    "quantity": it.quantity,
                    "estado_cocina": it.estado_cocina,
                })
                continue
            a_revertir.append((it, _item_options(db, it)))

        actor_id = user.id if user is not None else None
        reverse_order_items(db, a_revertir, actor_id, reference_id=order.id)
        revertidos = [str(it.id) for it, _ in a_revertir]

        order.status = "cancelada"
        db.add(OrderCancelLog(
            order_id=order.id, motivo=data.motivo,
            user_id=actor_id,
            user_name=user.name if user is not None else None,
            participant_id=participant.id if participant is not None else None,
        ))

        if perdidos:
            # La pérdida no genera movimiento de inventario (ya está descontada);
            # queda trazada en auditoría para el reporte de mermas.
            logger.warning(
                "Cancelación de orden %s con %d ítem(s) ya consumidos: pérdida sin reversa",
                order.id, len(perdidos),
            )
            record_audit(
                db,
                action="order.cancel.loss",
                entity="customer_orders",
                entity_id=order.id,
                user=user,
                payload={
                    "motivo": data.motivo,
                    "items_perdidos": perdidos,
                    "items_revertidos": revertidos,
                    "cancelado_por_comensal": participant.id is not None
                    if participant is not None else False,
                },
            )

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error cancelando la orden")
        raise

    return _reload_order(db, order_id)


# ------------------------------------------------------------- Liberar mesa

def close_participants(db: Session, ts: TableSession) -> int:
    """Echa a los comensales de una sesión y abandona sus carritos, **sin tocar la
    sesión**. Devuelve cuántos cerró.

    Se usa suelta cuando una sesión lleva demasiado abierta pero todavía tiene algo
    que cobrar: no interesa que nadie siga pidiendo con un token viejo, pero cerrar
    la sesión dejaría la mesa sin forma de cobrarse.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    participants = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.table_session_id == ts.id,
            SessionParticipant.status == "open",
        )
    ).scalars().all()

    for p in participants:
        p.status = "closed"
        if p.closed_at is None:
            p.closed_at = now
        for cart in db.execute(
            select(Cart).where(Cart.participant_id == p.id, Cart.status == "abierto")
        ).scalars():
            cart.status = "abandonado"

    return len(participants)


def close_table_sessions(
    db: Session, table_id: UUID, *, closed_by: User | None = None
) -> list[TableSession]:
    """Cierra en cascada las sesiones `active` de una mesa: la sesión de mesa, sus
    comensales y los carritos que quedaran abiertos.

    No hace commit (se une a la transacción del caller) ni valida órdenes
    pendientes — eso es responsabilidad de quien llama (`release_table`, el cierre
    con `billing_mode`, o el job de sesiones huérfanas, que pasa `closed_by=None`).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    sessions = db.execute(
        select(TableSession).where(
            TableSession.dining_table_id == table_id,
            TableSession.status == "active",
        )
    ).scalars().all()

    for ts in sessions:
        ts.status = "closed"
        if ts.closed_at is None:
            ts.closed_at = now
        if closed_by is not None:
            ts.closed_by_user_id = closed_by.id
            ts.closed_by_user_name = closed_by.name

        close_participants(db, ts)

    return sessions



def release_table(
    db: Session, table_id: UUID, *, closed_by: User | None = None
) -> DiningTable:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    blocking = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status.notin_(TERMINAL),
        )
    ).scalars().all()
    if blocking:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "La mesa tiene órdenes sin cerrar (paga o cancela primero).",
                "orders": [
                    {
                        "order_id": str(o.id),
                        "status": o.status,
                        "items": len([i for i in o.items if i.estado_cocina != "anulado"]),
                    }
                    for o in blocking
                ],
            },
        )

    try:
        table.status = "libre"
        close_table_sessions(db, table.id, closed_by=closed_by)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error liberando la mesa")
        raise

    db.refresh(table)
    return table
