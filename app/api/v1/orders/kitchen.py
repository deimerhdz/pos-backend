"""KDS (pantalla de cocina) y ciclo de vida del ítem (Fase 6).

La cocina es la fuente de verdad del `estado_cocina`, independiente del status
de pago. Editar un ítem ya en preparación/listo no es UPDATE silencioso: se anula
(`void`) y se crea uno nuevo con `void_de`."""
import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.option import Option
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.models.order_item_void_log import OrderItemVoidLog
from app.models.product_variant import ProductVariant
from app.api.v1.orders.consumption import deduct_order_item, reverse_order_item
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options
from app.api.v1.orders.schemas import (
    KdsItemResponse, KdsOrderResponse, KitchenTransitionIn, VoidItemIn,
)

logger = logging.getLogger(__name__)

ACTIVE_KITCHEN = ("pendiente", "en_preparacion", "listo")

# Transiciones legales hacia adelante (KDS).
_FORWARD = {
    "pendiente": "en_preparacion",
    "en_preparacion": "listo",
    "listo": "entregado",
}


def _item_options(db: Session, item: OrderItem) -> list[Option]:
    opt_ids = [o.option_id for o in item.options]
    if not opt_ids:
        return []
    return db.execute(select(Option).where(Option.id.in_(opt_ids))).scalars().all()


def list_kds(db: Session) -> list[KdsOrderResponse]:
    """Ítems activos de cocina, agrupados por orden/mesa (no por comensal)."""
    orders = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.status != "cancelada")
        .order_by(CustomerOrder.created_at)
    ).scalars().all()

    table_numbers = dict(db.execute(select(DiningTable.id, DiningTable.number)).all())

    result: list[KdsOrderResponse] = []
    for order in orders:
        active = [it for it in order.items if it.estado_cocina in ACTIVE_KITCHEN]
        if not active:
            continue
        result.append(KdsOrderResponse(
            order_id=order.id,
            dining_table_id=order.dining_table_id,
            table_number=table_numbers.get(order.dining_table_id),
            created_at=order.created_at,
            items=[KdsItemResponse.model_validate(it) for it in active],
        ))
    return result


def transition_kitchen(
    db: Session, item_id: UUID, data: KitchenTransitionIn
) -> OrderItem:
    item = get_or_404(db, OrderItem, item_id, "Order item not found")
    target = data.estado_cocina.value
    if _FORWARD.get(item.estado_cocina) != target:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "Transición de cocina inválida",
                "desde": item.estado_cocina,
                "hacia": target,
            },
        )
    item.estado_cocina = target
    db.commit()
    db.refresh(item)
    return item


def void_item(db: Session, item_id: UUID, data: VoidItemIn, user: User) -> CustomerOrder:
    item = db.execute(
        select(OrderItem)
        .options(selectinload(OrderItem.options))
        .where(OrderItem.id == item_id)
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order item not found")

    if item.estado_cocina == "anulado":
        raise HTTPException(status.HTTP_409_CONFLICT, "El ítem ya está anulado")
    if item.estado_cocina == "entregado":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ítem entregado: reclamo/reproceso, fuera de alcance",
        )

    order_id = item.order_id
    was_pendiente = item.estado_cocina == "pendiente"

    # Validar el reemplazo ANTES de mutar (para un 422 limpio si aplica).
    repl_variant = None
    repl_options: list[Option] = []
    if data.replacement is not None:
        repl_variant = get_or_404(
            db, ProductVariant, data.replacement.product_variant_id, "Variant not found"
        )
        if not repl_variant.active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {repl_variant.id}"
            )
        repl_options = load_valid_options(db, data.replacement.option_ids)

    try:
        item.estado_cocina = "anulado"

        if was_pendiente:
            # Cocina no consumió físicamente: se devuelve el inventario.
            reverse_order_item(db, item, _item_options(db, item), user.id, reference_id=order_id)

        db.add(OrderItemVoidLog(
            order_item_id=item.id, motivo=data.motivo,
            user_id=user.id, user_name=user.name,
        ))

        if data.replacement is not None:
            new_item = OrderItem(
                order_id=order_id,
                session_id=item.session_id,
                product_variant_id=repl_variant.id,
                quantity=data.replacement.quantity,
                unit_price=compute_line_price(repl_variant, repl_options),
                notes=data.replacement.notes,
                estado_cocina="pendiente",
                void_de=item.id,
            )
            db.add(new_item)
            db.flush()
            for opt in repl_options:
                db.add(OrderItemOption(order_item_id=new_item.id, option_id=opt.id))
            # Nuevo consumo (con lock; puede 400 y hacer rollback total).
            deduct_order_item(db, new_item, repl_options, user.id, reference_id=order_id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error anulando/reemplazando ítem")
        raise

    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one()
