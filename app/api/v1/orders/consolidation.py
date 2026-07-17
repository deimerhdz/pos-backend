"""Consolidación por mesero (Fase 4): agrupa los carritos abiertos de una mesa
en su única `order` abierta, generando `order_items` trazables por `session_id`
y descontando inventario por ítem insertado. Todo en una transacción."""
import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.dining_session import DiningSession
from app.models.cart import Cart
from app.models.cart_item import CartItem
from app.models.option import Option
from app.models.product_variant import ProductVariant
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.api.v1.orders.consumption import deduct_order_item
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options

logger = logging.getLogger(__name__)


def get_or_create_open_order(db: Session, table_id: UUID, user_id: UUID) -> CustomerOrder:
    """Regla de routing (decisión #6): la mesa tiene a lo sumo una order 'abierta'
    (índice parcial único). Si existe, se inserta ahí; si no (p.ej. la única está
    'bloqueada' en cobro), se crea una nueva 'abierta' (orden-hija)."""
    order = db.execute(
        select(CustomerOrder).where(
            CustomerOrder.dining_table_id == table_id,
            CustomerOrder.status == "abierta",
        )
    ).scalar_one_or_none()
    if order is None:
        order = CustomerOrder(
            dining_table_id=table_id,
            channel="waiter",
            status="abierta",
            user_id=user_id,
        )
        db.add(order)
        db.flush()
    return order


def consolidate_table(db: Session, table_id: UUID, user: User) -> CustomerOrder:
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    carts = db.execute(
        select(Cart)
        .join(DiningSession, Cart.session_id == DiningSession.id)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(
            DiningSession.dining_table_id == table.id,
            DiningSession.status == "open",
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

        for cart in carts_with_items:
            for ci in cart.items:
                item = OrderItem(
                    order_id=order.id,
                    session_id=cart.session_id,
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

                deduct_order_item(db, item, options, user.id, reference_id=order.id)

            cart.status = "confirmado"

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
    """Inserta un solo order_item en la orden de la mesa (add directo del mesero,
    Fase 5). Aplica la regla de routing (orden abierta o crea orden-hija) y el
    mismo descuento de inventario por ítem que la consolidación."""
    table = get_or_404(db, DiningTable, table_id, "Table not found")

    variant = get_or_404(db, ProductVariant, data.product_variant_id, "Variant not found")
    if not variant.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")
    options = load_valid_options(db, data.option_ids)

    try:
        order = get_or_create_open_order(db, table.id, user.id)

        item = OrderItem(
            order_id=order.id,
            session_id=None,  # ítem agregado por el mesero, sin sesión de comensal
            product_variant_id=variant.id,
            quantity=data.quantity,
            unit_price=compute_line_price(variant, options),
            notes=data.notes,
            estado_cocina="pendiente",
        )
        db.add(item)
        db.flush()
        for opt in options:
            db.add(OrderItemOption(order_item_id=item.id, option_id=opt.id))

        deduct_order_item(db, item, options, user.id, reference_id=order.id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error agregando ítem directo a la orden de la mesa")
        raise

    return _reload_order(db, order.id)
