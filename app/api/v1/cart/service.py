"""Service del carrito borrador de un comensal.

Unión a la sesión de mesa (con token) y CRUD de líneas con chequeo preventivo de
disponibilidad. **No descuenta inventario**: eso ocurre al confirmar el pedido."""
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.crud import get_or_404
from app.core.qr_context import close_participant
from app.core.qr_token import mint_session_token
from app.api.v1.table_sessions.service import try_release_if_empty
from app.models.dining_table import DiningTable
from app.models.table_session import TableSession
from app.models.session_participant import SessionParticipant
from app.models.cart import Cart
from app.models.cart_item import CartItem, CartItemOption
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem, OrderItemOption
from app.models.option import Option
from app.models.product_variant import ProductVariant
from app.api.v1.catalog.line_pricing import (
    check_availability,
    compute_line_price,
    load_valid_options,
    required_consumption,
)
from app.api.v1.orders.schemas import CancelIn
from app.api.v1.cart.schemas import (
    CartItemIn,
    CartItemUpdate,
    CartItemOptionResponse,
    CartItemResponse,
    CartResponse,
    SessionOpenResponse,
    SessionTableInfo,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------- Apertura sesión

def _get_or_create_table_session(db: Session, table: DiningTable) -> TableSession:
    """Sesión de mesa `active` de la mesa, creándola si no hay ninguna.

    Escanear el QR de una mesa ya ocupada **une** al comensal a la sesión en curso;
    no abre una segunda. El índice parcial `idx_active_session_per_table` garantiza
    la unicidad aunque dos escaneos entren a la vez."""
    ts = db.execute(
        select(TableSession).where(
            TableSession.dining_table_id == table.id,
            TableSession.status == "active",
        )
    ).scalar_one_or_none()
    if ts is None:
        ts = TableSession(dining_table_id=table.id, status="active")
        db.add(ts)
        db.flush()
    return ts


def _unique_display_label(db: Session, table_session_id: UUID, display_name: str) -> str:
    """Etiqueta desambiguada para cocina/staff. `display_name` no es único: si ya
    hay una "Ana" en la mesa, la siguiente se muestra como "Ana (2)"."""
    taken = set(db.execute(
        select(SessionParticipant.display_label).where(
            SessionParticipant.table_session_id == table_session_id
        )
    ).scalars())
    if display_name not in taken:
        return display_name
    n = 2
    while f"{display_name} ({n})" in taken:
        n += 1
    return f"{display_name} ({n})"


def open_session(
    db: Session, tenant_id: int, table: DiningTable, display_name: str
) -> SessionOpenResponse:
    """Une al comensal a la sesión activa de la mesa (o la abre si es el primero),
    crea su carrito y emite el token de sesión. Marca la mesa `ocupada`."""
    try:
        table_session = _get_or_create_table_session(db, table)

        participant = SessionParticipant(
            table_session_id=table_session.id,
            dining_table_id=table.id,
            display_name=display_name,
            display_label=_unique_display_label(db, table_session.id, display_name),
            status="open",
            expires_at=_now() + timedelta(minutes=settings.SESSION_TTL_MINUTES),
        )
        db.add(participant)
        db.flush()

        cart = Cart(participant_id=participant.id, status="abierto")
        db.add(cart)

        if table.status != "ocupada":
            table.status = "ocupada"

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error abriendo sesión de comensal")
        raise

    db.refresh(participant)
    db.refresh(cart)

    token = mint_session_token(
        tenant_id, table.id, participant.id, table_session.id,
        ttl_minutes=settings.SESSION_ABS_MAX_MINUTES,
    )
    return SessionOpenResponse(
        participant_id=participant.id,
        table_session_id=table_session.id,
        display_name=participant.display_name,
        display_label=participant.display_label,
        expires_at=participant.expires_at,
        table=SessionTableInfo.model_validate(table),
        cart_id=cart.id,
        session_token=token,
    )


# --------------------------------------------------------------- Carrito helpers

def _load_open_cart(db: Session, participant_id: UUID) -> Cart:
    cart = db.execute(
        select(Cart)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(Cart.participant_id == participant_id, Cart.status == "abierto")
    ).scalar_one_or_none()
    if cart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay carrito abierto para el comensal")
    return cart


def _get_or_create_open_cart(db: Session, participant_id: UUID) -> Cart:
    """Carrito 'abierto' vigente del comensal, creándolo si no existe. Al enviar
    un pedido el carrito anterior queda 'confirmado', así que el comensal reabre
    uno nuevo para la siguiente ronda. El índice parcial
    `idx_open_cart_per_participant` garantiza uno solo abierto por comensal."""
    cart = db.execute(
        select(Cart)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(Cart.participant_id == participant_id, Cart.status == "abierto")
    ).scalar_one_or_none()
    if cart is None:
        cart = Cart(participant_id=participant_id, status="abierto")
        db.add(cart)
        db.commit()
        db.refresh(cart)
    return cart


def _cart_consumption(
    db: Session, cart: Cart, *, exclude_item_id: UUID | None = None
) -> dict[UUID, Decimal]:
    """Consumo agregado (por insumo) de todas las líneas del carrito, opcionalmente
    excluyendo una línea (para el caso de edición)."""
    total: dict[UUID, Decimal] = defaultdict(Decimal)
    for item in cart.items:
        if exclude_item_id is not None and item.id == exclude_item_id:
            continue
        opt_ids = [o.option_id for o in item.options]
        options = db.execute(
            select(Option).where(Option.id.in_(opt_ids))
        ).scalars().all() if opt_ids else []
        for iid, need in required_consumption(
            db, item.product_variant_id, item.quantity, options
        ).items():
            total[iid] += need
    return total


def serialize_cart(cart: Cart, participant: SessionParticipant) -> CartResponse:
    items: list[CartItemResponse] = []
    total = Decimal("0")
    for it in cart.items:
        line_total = Decimal(it.unit_price) * it.quantity
        total += line_total
        items.append(CartItemResponse(
            id=it.id,
            product_variant_id=it.product_variant_id,
            quantity=it.quantity,
            unit_price=it.unit_price,
            line_total=line_total,
            notes=it.notes,
            options=[CartItemOptionResponse.model_validate(o) for o in it.options],
        ))
    return CartResponse(
        id=cart.id, participant_id=cart.participant_id, status=cart.status,
        display_name=participant.display_name,
        display_label=participant.display_label,
        total=total, items=items,
    )


def get_cart(db: Session, participant_id: UUID) -> CartResponse:
    """Carrito abierto del comensal, con su nombre. El nombre va en la respuesta
    porque el token de sesión no lo lleva: al recargar el menú, `GET /cart` es lo
    único que permite al front repintar el saludo."""
    participant = get_or_404(db, SessionParticipant, participant_id, "Comensal no encontrado")
    return serialize_cart(_get_or_create_open_cart(db, participant_id), participant)


# --------------------------------------------------------------- CRUD de líneas

def add_item(db: Session, participant_id: UUID, data: CartItemIn) -> CartResponse:
    cart = _get_or_create_open_cart(db, participant_id)

    variant = get_or_404(db, ProductVariant, data.product_variant_id, "Variant not found")
    if not variant.active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva: {variant.id}")

    options = load_valid_options(db, data.option_ids)

    # Disponibilidad: consumo del carrito actual + la línea nueva.
    required = _cart_consumption(db, cart)
    for iid, need in required_consumption(db, variant.id, data.quantity, options).items():
        required[iid] += need
    check_availability(db, required, extra_context="carrito")

    try:
        item = CartItem(
            cart_id=cart.id,
            product_variant_id=variant.id,
            quantity=data.quantity,
            unit_price=compute_line_price(variant, options),
            notes=data.notes,
        )
        db.add(item)
        db.flush()
        for opt in options:
            db.add(CartItemOption(cart_item_id=item.id, option_id=opt.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error agregando ítem al carrito")
        raise

    return get_cart(db, participant_id)


def update_item(
    db: Session, participant_id: UUID, item_id: UUID, data: CartItemUpdate
) -> CartResponse:
    cart = _load_open_cart(db, participant_id)
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea de carrito no encontrada")

    new_qty = data.quantity if data.quantity is not None else item.quantity
    if data.option_ids is not None:
        options = load_valid_options(db, data.option_ids)
    else:
        opt_ids = [o.option_id for o in item.options]
        options = db.execute(
            select(Option).where(Option.id.in_(opt_ids))
        ).scalars().all() if opt_ids else []

    variant = get_or_404(db, ProductVariant, item.product_variant_id, "Variant not found")

    # Disponibilidad: resto del carrito (sin esta línea) + la línea editada.
    required = _cart_consumption(db, cart, exclude_item_id=item_id)
    for iid, need in required_consumption(db, variant.id, new_qty, options).items():
        required[iid] += need
    check_availability(db, required, extra_context="carrito")

    try:
        item.quantity = new_qty
        item.unit_price = compute_line_price(variant, options)
        if data.notes is not None:
            item.notes = data.notes
        if data.option_ids is not None:
            for o in list(item.options):
                db.delete(o)
            db.flush()
            for opt in options:
                db.add(CartItemOption(cart_item_id=item.id, option_id=opt.id))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error editando ítem del carrito")
        raise

    return get_cart(db, participant_id)


def list_my_orders(db: Session, participant_id: UUID) -> list[CustomerOrder]:
    """Pedidos que envió este comensal, para restaurar la UI al reingresar. Se
    devuelve desde el backend en vez de reconstruirlo con estado local del
    navegador, que es justo lo que se pierde al recargar o cambiar de móvil."""
    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.participant_id == participant_id)
        .order_by(CustomerOrder.created_at.desc())
    ).scalars().all()


def cancel_my_order(
    db: Session, participant: SessionParticipant, order_id: UUID, motivo: str
) -> CustomerOrder:
    """Cancelación por el propio comensal. Solo hasta que cocina empieza:

    - `recibida`: siempre (no se descontó nada);
    - `abierta`: solo si **todos** sus ítems siguen `pendiente`;
    - a partir de `en_preparacion` el insumo ya se combinó y la decisión de cuánto
      se pierde es del staff, no del cliente.

    Delega la política de inventario en `checkout.cancel_order`, que ya sabe qué
    revertir y qué contar como pérdida."""
    from app.api.v1.orders import checkout

    order = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(CustomerOrder.id == order_id)
    ).scalar_one_or_none()
    if order is None or order.participant_id != participant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pedido no encontrado")

    if order.status in checkout.TERMINAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"El pedido ya es terminal (status={order.status})"
        )
    if order.status != "recibida":
        en_cocina = [
            it for it in order.items
            if it.estado_cocina not in ("pendiente", "anulado")
        ]
        if en_cocina or order.status != "abierta":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "El pedido ya está en preparación; pide al personal que lo cancele.",
            )

    result = checkout.cancel_order(
        db, order_id, CancelIn(motivo=motivo), user=None, participant=participant
    )

    # Si era el último pedido y el comensal ya se había ido, la mesa queda vacía:
    # cubre el "pedí, me arrepentí y me fui" sin esperar al barrido.
    if try_release_if_empty(db, participant.table_session_id):
        db.commit()

    return result


def leave_session(db: Session, participant: SessionParticipant) -> None:
    """El comensal se va de la mesa.

    Cierra su carrito y, si era el último y no quedó nada que cobrar, libera la
    mesa en el acto. No falla nunca: salir es idempotente y el comensal ya se está
    marchando — un error aquí no le sirve de nada a nadie.
    """
    close_participant(db, participant)
    try_release_if_empty(db, participant.table_session_id)
    db.commit()


def submit_cart(db: Session, participant: SessionParticipant) -> CustomerOrder:
    """Envía el carrito del comensal como pedido: crea una `CustomerOrder` en
    estado `recibida` con sus líneas y cierra el carrito.

    **No toca inventario.** El descuento ocurre cuando el staff confirma el pedido
    (`POST /orders/{id}/confirm`); hasta entonces el comensal puede cancelarlo sin
    ningún movimiento de stock. El chequeo de disponibilidad de aquí es preventivo
    (sin lock ni reserva): la validación real y atómica es la de la confirmación.

    El comensal puede enviar varios pedidos en la misma sesión: al enviar, este
    carrito queda `confirmado` y `_get_or_create_open_cart` le abre otro."""
    cart = _load_open_cart(db, participant.id)
    if not cart.items:
        raise HTTPException(status.HTTP_409_CONFLICT, "El carrito está vacío")

    check_availability(db, _cart_consumption(db, cart), extra_context="envío de pedido")

    try:
        order = CustomerOrder(
            table_session_id=participant.table_session_id,
            participant_id=participant.id,
            dining_table_id=participant.dining_table_id,
            customer_name=participant.display_label or participant.display_name,
            channel="qr",
            status="recibida",
            user_id=None,  # lo envió el comensal, no un usuario del sistema
        )
        db.add(order)
        db.flush()

        for ci in cart.items:
            item = OrderItem(
                order_id=order.id,
                participant_id=participant.id,
                product_variant_id=ci.product_variant_id,
                quantity=ci.quantity,
                unit_price=ci.unit_price,  # snapshot copiado del carrito
                notes=ci.notes,
                estado_cocina="pendiente",
            )
            db.add(item)
            db.flush()
            for o in ci.options:
                db.add(OrderItemOption(order_item_id=item.id, option_id=o.option_id))

        cart.status = "confirmado"
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error enviando el pedido del comensal")
        raise

    return db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items).selectinload(OrderItem.options))
        .where(CustomerOrder.id == order.id)
    ).scalar_one()


def remove_item(db: Session, participant_id: UUID, item_id: UUID) -> CartResponse:
    cart = _load_open_cart(db, participant_id)
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea de carrito no encontrada")
    db.delete(item)
    db.commit()
    return get_cart(db, participant_id)
