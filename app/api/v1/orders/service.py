"""Service de comandas del staff (mostrador/mesero): crea una `customer_order`
con sus líneas y opciones, tomando un snapshot del precio.

**Consume inventario al crear, salvo `hold_for_payment`.** Por defecto la
comanda nace en `abierta`, o sea ya confirmada: no vuelve a pasar por
`confirm_order`, así que si no descontara aquí no descontaría nunca —cobrarla
tampoco lo hace— y el stock quedaría sobrestimado en silencio. Es el mismo
punto de descuento que la confirmación, por la otra puerta.

Con `hold_for_payment=True` (spec 028, T013) la comanda nace `recibida` en su
lugar, sin tocar inventario: el staff cobra primero
(`checkout.checkout_and_send`) y ese es el nuevo punto de descuento para este
camino — mismo criterio de "un único punto de descuento", solo que movido.

El pedido anónimo por QR **no** entra por aquí: llega como `recibida` vía
`/cart/submit` y lo descuenta el staff al confirmarlo."""
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.models.product_variant import ProductVariant
from app.models.option import Option
from app.catalog_engine import ChosenOption
from app.models.session_participant import SessionParticipant
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.sale import Sale
from app.models.table_session import TableSession
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options
from app.api.v1.orders.consolidation import get_or_create_table_session_id
from app.api.v1.orders.consumption import deduct_order_items
from app.api.v1.orders.schemas import OrderChannel, OrderCreate, OrderType

logger = logging.getLogger(__name__)

#: Estados en los que un pedido todavía "está pasando" por la mesa: ni
#: recién llegado y sin resolver, ni terminal. Mismo conjunto que
#: `cart.service._NON_TERMINAL_ORDER_STATUSES` — se repite aquí (en vez de
#: importarlo) porque cada módulo de este paquete define el suyo (ver
#: `checkout.TERMINAL`), y `cart.service` ya importa de `table_sessions.service`,
#: que a su vez depende de `orders.checkout`.
_NON_TERMINAL_ORDER_STATUSES = ("recibida", "abierta", "bloqueada")

#: Combinaciones canal × tipo de orden con sentido de negocio (spec 055,
#: FR-006; research.md D4) — validado solo aquí: es el único punto donde
#: ambos valores llegan como datos arbitrarios de un llamador (`OrderCreate`).
#: El flujo QR (`cart.service.submit_cart`) y el de consolidación/mesero
#: (`orders.consolidation.get_or_create_open_order`) construyen su propia
#: combinación fija y ya válida por construcción, sin pasar por aquí.
_COMBINACIONES_CANAL_TIPO_ORDEN: dict[OrderChannel, frozenset[OrderType]] = {
    OrderChannel.POS: frozenset({OrderType.DINE_IN, OrderType.TAKEAWAY, OrderType.DELIVERY}),
    OrderChannel.QR_MENU: frozenset({OrderType.DINE_IN}),
    OrderChannel.WHATSAPP: frozenset({OrderType.TAKEAWAY, OrderType.DELIVERY}),
    OrderChannel.API: frozenset({OrderType.TAKEAWAY, OrderType.DELIVERY}),
}


def order_has_sale(db: Session, order_id: UUID) -> bool:
    """¿Ya existe una `Sale` para este pedido? (spec 029, D2/D3 de research.md)

    Es la señal correcta de "ya está pagado" — a diferencia de
    `CustomerOrder.status`, que nunca llega a `"pagada"` en los caminos QR y
    de mostrador vigentes (`checkout_and_send`/`_confirm_order_impl` dejan la
    orden en `"abierta"` a propósito, con la venta ya emitida). Mismo patrón
    de subconsulta ya probado en `has_billable_orders`
    (`table_sessions/service.py`), sin tocar esa función."""
    return db.execute(
        select(Sale.id).where(Sale.customer_order_id == order_id).limit(1)
    ).scalar() is not None


def paid_order_ids(db: Session, order_ids: list[UUID]) -> set[UUID]:
    """Versión en bloque de `order_has_sale`, para no hacer N consultas al
    serializar un listado de pedidos (spec 029, D2 de research.md)."""
    if not order_ids:
        return set()
    rows = db.execute(
        select(Sale.customer_order_id).where(Sale.customer_order_id.in_(order_ids))
    ).scalars().all()
    return set(rows)


def list_orders(
    db: Session, status_filter: str | None = None, active_sessions_only: bool = False,
) -> list[CustomerOrder]:
    """Listado de comandas para la Terminal de Mesas (`GET /orders`).

    `active_sessions_only=True` (spec 029, hotfix): descarta pedidos ya
    pagados cuya `TableSession` ya cerró — sin esto, un pedido de una visita
    ya cobrada y liberada quedaba visible para siempre (ligado a la misma
    `dining_table_id` física) y reaparecía mezclado con la sesión nueva en
    cuanto la mesa se ocupaba de nuevo, porque `status` nunca llega a
    `"pagada"` en los caminos QR/mostrador vigentes (D2 de research.md).
    Mismo patrón de subconsulta que `has_billable_orders`/`_billable_orders`
    (`table_sessions/service.py`), expuesto aquí para el listado general.

    Un pedido **sin pagar** se conserva aunque su sesión ya haya cerrado — es
    el caso huérfano real que `billOrphan` (frontend) existe para señalar,
    no algo que ocultar. Uno sin `table_session_id` (mostrador puro, sin
    mesa) tampoco se toca."""
    q = select(CustomerOrder).options(
        selectinload(CustomerOrder.items).selectinload(OrderItem.options),
        selectinload(CustomerOrder.payment_attempts)
        .selectinload(OrderPaymentAttempt.payment_method),
    ).order_by(CustomerOrder.created_at.desc())
    if status_filter is not None:
        q = q.where(CustomerOrder.status == status_filter)
    if active_sessions_only:
        inactive_sessions = select(TableSession.id).where(TableSession.status != "active")
        pedidos_pagados = select(Sale.customer_order_id).where(Sale.customer_order_id.isnot(None))
        q = q.where(
            or_(
                CustomerOrder.table_session_id.is_(None),
                CustomerOrder.table_session_id.notin_(inactive_sessions),
                CustomerOrder.id.notin_(pedidos_pagados),
            )
        )
    return db.execute(q).scalars().all()


def create_order(db: Session, data: OrderCreate, user_id: UUID | None) -> CustomerOrder:
    # T013 (spec 028): 'hold_for_payment' es el modo "cobra primero, envía
    # después" del mostrador/mesero. El canal QR ya tiene su propio flujo
    # 'recibida' (vía /cart/submit, con OrderPaymentAttempt) — mezclarlo con
    # esta bandera duplicaría ese camino con reglas distintas.
    if data.hold_for_payment and data.channel is OrderChannel.QR_MENU:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "hold_for_payment solo aplica a comandas de mostrador/mesero, no a pedidos por QR.",
        )

    # spec 055, research.md D5: un pedido para llevar o a domicilio nunca
    # lleva mesa asociada.
    if data.order_type in (OrderType.TAKEAWAY, OrderType.DELIVERY) and data.dining_table_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un pedido para llevar o a domicilio no puede tener una mesa asociada.",
        )

    # spec 055, FR-006/FR-007.
    if data.order_type not in _COMBINACIONES_CANAL_TIPO_ORDEN[data.channel]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"La combinación de canal '{data.channel.value}' y tipo de orden "
            f"'{data.order_type.value}' no es válida.",
        )

    # spec 056, FR-007: un pedido a domicilio requiere nombre del cliente,
    # dirección y valor del domicilio (el teléfono queda fuera a propósito,
    # FR-008 — siempre opcional).
    if data.order_type is OrderType.DELIVERY and (
        not (data.customer_name or "").strip()
        or not (data.delivery_address or "").strip()
        or data.delivery_fee is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un pedido a domicilio requiere nombre del cliente, dirección y "
            "valor del domicilio.",
        )

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

    # Un pedido de mesa sin sesión no entra en ninguna cuenta y no se podría
    # cobrar, así que se abre la sesión si aún no existe.
    table_session_id = (
        participant.table_session_id if participant is not None
        else get_or_create_table_session_id(db, table_id) if table_id is not None
        else None
    )

    # T014 (spec 028, FR-013): una mesa no mezcla orígenes de pedido a la vez.
    # Si ya hay un pedido QR activo en la sesión, una comanda de
    # mostrador/mesero no puede abrirse encima (y viceversa: ver
    # `cart.service.submit_cart`, T015).
    if table_session_id is not None and data.channel is not OrderChannel.QR_MENU:
        conflicto_qr = db.execute(
            select(CustomerOrder.id).where(
                CustomerOrder.table_session_id == table_session_id,
                CustomerOrder.channel == "QR_MENU",
                CustomerOrder.status.in_(_NON_TERMINAL_ORDER_STATUSES),
            ).limit(1)
        ).scalar()
        if conflicto_qr is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Esta mesa ya tiene un pedido activo por QR; ciérralo antes de "
                "abrir una comanda de mostrador/mesero.",
            )

    try:
        order = CustomerOrder(
            participant_id=data.participant_id,
            table_session_id=table_session_id,
            dining_table_id=table_id,
            customer_name=customer_name,
            channel=data.channel.value,
            order_type=data.order_type.value,
            delivery_address=data.delivery_address,
            delivery_phone=data.delivery_phone,
            delivery_fee=data.delivery_fee,
            # spec 073 (FR-008, A-70): el instante de vigencia de promociones se
            # congela una sola vez, aquí, al crear el pedido — aware UTC (la
            # columna es DateTime(timezone=True); NO `.replace(tzinfo=None)`,
            # ver data-model.md). Nunca se vuelve a tocar.
            promotion_evaluated_at=datetime.now(timezone.utc),
            # T013: con hold_for_payment nace 'recibida', igual que un pedido
            # QR sin confirmar — no compromete stock ni es visible para cocina
            # hasta que se cobre (`checkout.checkout_and_send`).
            status="recibida" if data.hold_for_payment else "abierta",
            user_id=user_id,
            notes=data.notes,
        )
        db.add(order)
        db.flush()

        entries: list[tuple[OrderItem, list[ChosenOption]]] = []
        for line in data.items:
            variant = get_or_404(db, ProductVariant, line.product_variant_id, "Variant not found")
            if not variant.active:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")

            # Deduplica, exige que estén activas y valida la selección contra los
            # grupos del producto. Antes este bucle cargaba las opciones a mano y se
            # saltaba las tres cosas.
            options = load_valid_options(db, line.options, variant=variant)

            item = OrderItem(
                order_id=order.id,
                participant_id=data.participant_id,
                product_variant_id=variant.id,
                quantity=line.quantity,
                notes=line.notes,
            )
            db.add(item)
            db.flush()

            for chosen in options:
                db.add(OrderItemOption(
                    order_item_id=item.id, option_id=chosen.option.id, quantity=chosen.quantity,
                ))

            item.unit_price = compute_line_price(variant, options)
            entries.append((item, options))

        if not data.hold_for_payment:
            # Nace confirmada, así que compromete stock aquí y ahora. Si falta
            # un insumo (o alguna variante no tiene receta) revienta la
            # transacción entera y no se crea la comanda: mejor no venderla
            # que venderla sin descontar.
            #
            # Con hold_for_payment=True (T013) esto se salta a propósito: la
            # comanda nace 'recibida' y el descuento se hace en
            # `checkout.checkout_and_send`, al cobrar — mismo punto único de
            # descuento que usa el flujo QR (`_confirm_order_impl`), por la
            # otra puerta.
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
