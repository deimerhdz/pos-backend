"""Ciclo de vida del ítem: preparación y anulación.

`estado_cocina` es independiente del status de pago de la orden y lo mueve la
terminal de mesas (antes había un KDS aparte, ya deprecado). Editar un ítem ya
en preparación/listo no es UPDATE silencioso: se anula (`void`) y se crea uno
nuevo con `void_de`."""
import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.core.models import User
from app.models.option import Option
from app.models.customer_order import CustomerOrder
from app.models.order_item import EN_CURSO, OrderItem, OrderItemOption
from app.models.order_item_void_log import OrderItemVoidLog
from app.models.product_variant import ProductVariant
from app.api.v1.orders import service
from app.api.v1.orders.consumption import deduct_order_items, reverse_order_items
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options
from app.api.v1.orders.schemas import KitchenTransitionIn, VoidItemIn

logger = logging.getLogger(__name__)

# Transiciones legales, siempre hacia adelante. `pendiente → listo` es el salto
# directo que usa el botón de un toque de la terminal: quien toma el pedido es
# quien lo prepara, y obligarle a pasar por 'en_preparacion' es un clic de más.
_ALLOWED: dict[str, frozenset[str]] = {
    "pendiente": frozenset({"en_preparacion", "listo"}),
    "en_preparacion": frozenset({"listo"}),
}


def _item_options(db: Session, item: OrderItem) -> list[Option]:
    opt_ids = [o.option_id for o in item.options]
    if not opt_ids:
        return []
    return db.execute(select(Option).where(Option.id.in_(opt_ids))).scalars().all()


def transition_kitchen(
    db: Session, item_id: UUID, data: KitchenTransitionIn
) -> OrderItem:
    item = get_or_404(db, OrderItem, item_id, "Order item not found")
    target = data.estado_cocina.value
    if target not in _ALLOWED.get(item.estado_cocina, frozenset()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "Transición de preparación inválida",
                "desde": item.estado_cocina,
                "hacia": target,
            },
        )
    item.estado_cocina = target
    db.commit()
    db.refresh(item)
    return item


def mark_order_ready(db: Session, order_id: UUID) -> tuple[CustomerOrder, list[OrderItem]]:
    """Pasa a `listo` todos los ítems en curso de la orden, en un solo commit.

    Es lo que necesita la terminal para cobrar sin ir ítem por ítem: la
    alternativa era una petición por transición y por ítem. Devuelve también los
    ítems que cambiaron, porque el router emite un evento por cada uno."""
    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    if order.status in ("pagada", "cancelada"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"La orden ya es terminal (status={order.status})",
        )

    cambiados = [it for it in order.items if it.estado_cocina in EN_CURSO]
    if not cambiados:
        return order, []

    for it in cambiados:
        it.estado_cocina = "listo"
    db.commit()
    db.refresh(order)
    return order, cambiados


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

    order_id = item.order_id
    order_status = db.execute(
        select(CustomerOrder.status).where(CustomerOrder.id == order_id)
    ).scalar_one()
    # spec 029 (Historia 1, A-16): un pedido pagado se asume entregado y ya no
    # es anulable. `status == "pagada"` cubre el camino legado
    # (block_order → pay_order); `order_has_sale` cubre el camino QR/mostrador
    # vigente, que deja la orden en "abierta" con la Sale ya emitida (D2/D3 de
    # research.md) — a diferencia de `mark_order_ready`, que solo mira
    # `status`, aquí eso no basta.
    if order_status == "pagada" or service.order_has_sale(db, order_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "El pedido ya fue pagado y no puede anularse"
        )

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
        repl_options = load_valid_options(
            db, data.replacement.option_ids, variant=repl_variant
        )

    try:
        item.estado_cocina = "anulado"

        if was_pendiente:
            # Cocina no consumió físicamente: se devuelve el inventario.
            reverse_order_items(
                db, [(item, _item_options(db, item))], user.id, reference_id=order_id
            )

        db.add(OrderItemVoidLog(
            order_item_id=item.id, motivo=data.motivo,
            user_id=user.id, user_name=user.name,
        ))

        if data.replacement is not None:
            new_item = OrderItem(
                order_id=order_id,
                participant_id=item.participant_id,
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
            deduct_order_items(
                db, [(new_item, repl_options)], user.id, reference_id=order_id
            )

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
