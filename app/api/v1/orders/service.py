"""Service de comandas del staff (mostrador/mesero): crea una `customer_order`
con sus líneas y opciones, tomando un snapshot del precio.

**Consume inventario al crear.** La comanda nace en `abierta`, o sea ya
confirmada: no vuelve a pasar por `confirm_order`, así que si no descontara aquí
no descontaría nunca —cobrarla tampoco lo hace— y el stock quedaría sobrestimado
en silencio. Es el mismo punto de descuento que la confirmación, por la otra
puerta.

El pedido anónimo por QR **no** entra por aquí: llega como `recibida` vía
`/cart/submit` y lo descuenta el staff al confirmarlo."""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.models.product_variant import ProductVariant
from app.models.option import Option
from app.models.session_participant import SessionParticipant
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.api.v1.orders.consolidation import get_or_create_table_session_id
from app.api.v1.orders.consumption import deduct_order_items
from app.api.v1.orders.schemas import OrderCreate
from app.api.v1.promotions import service as promotions

logger = logging.getLogger(__name__)


def create_order(db: Session, data: OrderCreate, user_id: UUID | None) -> CustomerOrder:
    customer_name = data.customer_name
    table_id = data.dining_table_id

    participant = None
    if data.participant_id is not None:
        participant = get_or_404(
            db, SessionParticipant, data.participant_id, "Participant not found"
        )
        if participant.status != "open":
            raise HTTPException(status.HTTP_409_CONFLICT, "El comensal ya no está en la mesa")
        table_id = participant.dining_table_id
        customer_name = customer_name or participant.display_label or participant.display_name

    if table_id is not None and participant is None:
        get_or_404(db, DiningTable, table_id, "Table not found")

    try:
        order = CustomerOrder(
            participant_id=data.participant_id,
            # Un pedido de mesa sin sesión no entra en ninguna cuenta y no se
            # podría cobrar, así que se abre la sesión si aún no existe.
            table_session_id=(
                participant.table_session_id if participant is not None
                else get_or_create_table_session_id(db, table_id) if table_id is not None
                else None
            ),
            dining_table_id=table_id,
            customer_name=customer_name,
            channel=data.channel.value,
            status="abierta",
            user_id=user_id,
            notes=data.notes,
        )
        db.add(order)
        db.flush()

        entries: list[tuple[OrderItem, list[Option]]] = []
        now = datetime.now(timezone.utc)
        for line in data.items:
            if line.combo_id is not None:
                # Un combo se expande en sus componentes reales a precio normal;
                # el ahorro se calcula al cobrar, no aquí (ver consolidation.py).
                for component in promotions.expand_combo(db, line.combo_id, line.quantity, now):
                    item = OrderItem(
                        order_id=order.id,
                        participant_id=data.participant_id,
                        product_variant_id=component.product_variant_id,
                        quantity=component.quantity,
                        unit_price=component.unit_price,
                        notes=line.notes,
                        combo_id=line.combo_id,
                    )
                    db.add(item)
                    db.flush()
                    entries.append((item, []))
                continue

            variant = get_or_404(db, ProductVariant, line.product_variant_id, "Variant not found")
            if not variant.active:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")

            unit_price = Decimal(variant.price)
            item = OrderItem(
                order_id=order.id,
                participant_id=data.participant_id,
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
            entries.append((item, [db.get(Option, oid) for oid in seen]))

        # Nace confirmada, así que compromete stock aquí y ahora. Si falta un
        # insumo (o alguna variante no tiene receta) revienta la transacción
        # entera y no se crea la comanda: mejor no venderla que venderla sin
        # descontar.
        deduct_order_items(db, entries, user_id, reference_id=order.id)

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
