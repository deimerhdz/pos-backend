"""Consolidación por mesero: agrupa los carritos abiertos de una mesa en la `order`
del mesero, generando `order_items` trazables por `participant_id` y descontando
inventario en bloque. Todo en una transacción.

Es la vía del mesero, alternativa a que el comensal envíe su propio pedido desde
el QR (`POST /cart/submit` → `POST /orders/{id}/confirm`). A diferencia de esa,
aquí la línea nace ya confirmada: el staff la mete y el stock se descuenta."""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.table_session import TableSession
from app.models.session_participant import SessionParticipant
from app.models.cart import Cart
from app.models.cart_item import CartItem
from app.models.option import Option
from app.models.product_variant import ProductVariant
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.api.v1.orders.consumption import deduct_order_items
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options
from app.api.v1.promotions import service as promotions

logger = logging.getLogger(__name__)


def active_table_session_id(db: Session, table_id: UUID) -> UUID | None:
    """Id de la `table_session` activa de la mesa, si la hay. Los pedidos que el
    mesero crea sobre una mesa ocupada cuelgan de la misma sesión que los del QR,
    para que la cuenta salga completa."""
    return db.execute(
        select(TableSession.id).where(
            TableSession.dining_table_id == table_id,
            TableSession.status == "active",
        )
    ).scalar_one_or_none()


def get_or_create_table_session_id(db: Session, table_id: UUID) -> UUID:
    """Sesión activa de la mesa, abriéndola si no la hay.

    La unidad de cobro es la `table_session`: un pedido sin ella no entra en
    ninguna cuenta y no se puede cobrar nunca. Hasta ahora la sesión solo nacía
    cuando un comensal escaneaba el QR, así que las mesas que atiende el mesero
    quedaban descolgadas. El índice parcial `idx_active_session_per_table`
    garantiza que no haya dos activas aunque dos meseros pidan a la vez."""
    existing = active_table_session_id(db, table_id)
    if existing is not None:
        return existing

    ts = TableSession(dining_table_id=table_id, status="active")
    db.add(ts)
    db.flush()

    table = db.get(DiningTable, table_id)
    if table is not None and table.status != "ocupada":
        # Simetría con el cierre de sesión, que devuelve la mesa a 'libre'.
        table.status = "ocupada"
    return ts.id


def get_or_create_open_order(db: Session, table_id: UUID, user_id: UUID) -> CustomerOrder:
    """Pedido `abierta` de la mesa donde acumular las líneas del mesero, creándolo
    si no hay ninguno (p.ej. porque el único está `bloqueada` en cobro).

    Ya no existe el índice único de "una orden abierta por mesa": la mesa puede
    tener varios pedidos a la vez (uno por comensal). Este helper solo agrupa las
    líneas que **añade el staff**; los pedidos del QR llegan por su propia vía y
    se agrupan por `table_session_id`. La consulta ordena por `created_at` para
    ser determinista si hubiera más de uno."""
    order = db.execute(
        select(CustomerOrder)
        .where(
            CustomerOrder.dining_table_id == table_id,
            CustomerOrder.status == "abierta",
            CustomerOrder.channel == "waiter",
        )
        .order_by(CustomerOrder.created_at)
        .limit(1)
    ).scalar_one_or_none()
    session_id = get_or_create_table_session_id(db, table_id)
    if order is None:
        order = CustomerOrder(
            dining_table_id=table_id,
            table_session_id=session_id,
            channel="waiter",
            status="abierta",
            user_id=user_id,
        )
        db.add(order)
        db.flush()
    elif order.table_session_id is None:
        # Orden creada cuando la mesa aún no tenía sesión: sin esto seguiría fuera
        # de la cuenta y no habría forma de cobrarla.
        order.table_session_id = session_id
    return order


def consolidate_table(db: Session, table_id: UUID, user: User) -> CustomerOrder:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    carts = db.execute(
        select(Cart)
        .join(SessionParticipant, Cart.participant_id == SessionParticipant.id)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(
            SessionParticipant.dining_table_id == table.id,
            SessionParticipant.status == "open",
            Cart.status == "abierto",
        )
    ).scalars().all()

    carts_with_items = [c for c in carts if c.items]
    if not carts_with_items:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No hay carritos con ítems para consolidar"
        )

    try:
        order = get_or_create_open_order(db, table.id, user.id)

        # Se insertan todas las líneas primero y el inventario se descuenta en
        # bloque al final: así los locks de insumo se toman en orden canónico y
        # dos consolidaciones concurrentes no pueden deadlockear.
        entries: list[tuple[OrderItem, list[Option]]] = []
        for cart in carts_with_items:
            for ci in cart.items:
                item = OrderItem(
                    order_id=order.id,
                    participant_id=cart.participant_id,
                    product_variant_id=ci.product_variant_id,
                    quantity=ci.quantity,
                    unit_price=ci.unit_price,  # snapshot copiado del carrito
                    notes=ci.notes,
                    estado_cocina="pendiente",
                )
                db.add(item)
                db.flush()

                opt_ids = [o.option_id for o in ci.options]
                options = db.execute(
                    select(Option).where(Option.id.in_(opt_ids))
                ).scalars().all() if opt_ids else []
                for opt in options:
                    db.add(OrderItemOption(order_item_id=item.id, option_id=opt.id))

                entries.append((item, options))

            cart.status = "confirmado"

        deduct_order_items(db, entries, user.id, reference_id=order.id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error consolidando carritos de la mesa")
        raise

    return _reload_order(db, order.id)


def _reload_order(db: Session, order_id: UUID) -> CustomerOrder:
    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one()


def add_item_to_table(db: Session, table_id: UUID, data, user: User) -> CustomerOrder:
    """Inserta uno o varios order_items en la orden de la mesa (add directo del
    mesero, Fase 5). Aplica la regla de routing (orden abierta o crea orden-hija)
    y el mismo descuento de inventario por ítem que la consolidación.

    Un `combo_id` se expande en sus componentes reales a precio normal (igual
    que `cart/service.py::_add_combo`); el ahorro del combo no se calcula aquí,
    se realiza al cobrar (`pay_order`/`close_session`)."""
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    if data.combo_id is not None:
        components = promotions.expand_combo(
            db, data.combo_id, data.quantity, datetime.now(timezone.utc)
        )
        lines = [(c.product_variant_id, c.quantity, [], c.unit_price, data.combo_id) for c in components]
    else:
        variant = get_or_404(db, ProductVariant, data.product_variant_id, "Variant not found")
        if not variant.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")
        options = load_valid_options(db, data.option_ids)
        lines = [(variant.id, data.quantity, options, compute_line_price(variant, options), None)]

    try:
        order = get_or_create_open_order(db, table.id, user.id)

        entries: list[tuple[OrderItem, list[Option]]] = []
        for product_variant_id, quantity, options, unit_price, combo_id in lines:
            item = OrderItem(
                order_id=order.id,
                participant_id=None,  # ítem agregado por el mesero, sin comensal asignado
                product_variant_id=product_variant_id,
                quantity=quantity,
                unit_price=unit_price,
                notes=data.notes,
                combo_id=combo_id,
                estado_cocina="pendiente",
            )
            db.add(item)
            db.flush()
            for opt in options:
                db.add(OrderItemOption(order_item_id=item.id, option_id=opt.id))
            entries.append((item, options))

        deduct_order_items(db, entries, user.id, reference_id=order.id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error agregando ítem directo a la orden de la mesa")
        raise

    return _reload_order(db, order.id)
