"""Service de comandas: crea una `customer_order` con sus líneas y opciones,
tomando un snapshot del precio (variante + extra de opciones). No consume
inventario: eso ocurre al cobrar la venta (módulo sales)."""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.models.product_variant import ProductVariant
from app.models.option import Option
from app.models.dining_session import DiningSession
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.api.v1.orders.schemas import OrderCreate

logger = logging.getLogger(__name__)


def create_order(db: Session, data: OrderCreate, user_id: UUID | None) -> CustomerOrder:
    customer_name = data.customer_name
    table_id = data.dining_table_id

    session = None
    if data.dining_session_id is not None:
        session = get_or_404(db, DiningSession, data.dining_session_id, "Session not found")
        if session.status != "open":
            raise HTTPException(status.HTTP_409_CONFLICT, "La sesión está cerrada")
        table_id = session.dining_table_id
        customer_name = customer_name or session.customer_name

    if table_id is not None and session is None:
        get_or_404(db, DiningTable, table_id, "Table not found")

    try:
        order = CustomerOrder(
            dining_session_id=data.dining_session_id,
            dining_table_id=table_id,
            customer_name=customer_name,
            channel=data.channel.value,
            status="abierta",
            user_id=user_id,
            notes=data.notes,
        )
        db.add(order)
        db.flush()

        for line in data.items:
            variant = get_or_404(db, ProductVariant, line.product_variant_id, "Variant not found")
            if not variant.active:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")

            unit_price = Decimal(variant.price)
            item = OrderItem(
                order_id=order.id,
                session_id=data.dining_session_id,
                product_variant_id=variant.id,
                quantity=line.quantity,
                notes=line.notes,
            )
            db.add(item)
            db.flush()

            seen: set[UUID] = set()
            for opt_id in line.option_ids:
                if opt_id in seen:
                    continue
                option = get_or_404(db, Option, opt_id, f"Option {opt_id} not found")
                unit_price += Decimal(option.extra_price)
                db.add(OrderItemOption(order_item_id=item.id, option_id=opt_id))
                seen.add(opt_id)

            item.unit_price = unit_price

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error creando comanda")
        raise

    return db.execute(
        select(CustomerOrder).where(CustomerOrder.id == order.id)
    ).scalar_one()
