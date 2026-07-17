"""Service del carrito por comensal (Fase 3).

Apertura de sesión (con token) y CRUD de líneas con chequeo preventivo de
disponibilidad. No consolida ni descuenta inventario (eso es Fase 4)."""
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
from app.core.qr_token import mint_session_token
from app.models.dining_table import DiningTable
from app.models.dining_session import DiningSession
from app.models.cart import Cart
from app.models.cart_item import CartItem, CartItemOption
from app.models.option import Option
from app.models.product_variant import ProductVariant
from app.api.v1.catalog.line_pricing import (
    check_availability,
    compute_line_price,
    load_valid_options,
    required_consumption,
)
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

def open_session(
    db: Session, tenant_id: int, table: DiningTable, customer_name: str
) -> SessionOpenResponse:
    """Crea una sesión de comensal + su carrito abierto y emite el token de
    sesión. Marca la mesa `ocupada`."""
    try:
        session = DiningSession(
            dining_table_id=table.id,
            customer_name=customer_name,
            status="open",
            expires_at=_now() + timedelta(minutes=settings.SESSION_TTL_MINUTES),
        )
        db.add(session)
        db.flush()

        cart = Cart(session_id=session.id, status="abierto")
        db.add(cart)

        if table.status != "ocupada":
            table.status = "ocupada"

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error abriendo sesión de carrito")
        raise

    db.refresh(session)
    db.refresh(cart)

    token = mint_session_token(
        tenant_id, table.id, session.id,
        ttl_minutes=settings.SESSION_ABS_MAX_MINUTES,
    )
    return SessionOpenResponse(
        session_id=session.id,
        customer_name=session.customer_name,
        expires_at=session.expires_at,
        table=SessionTableInfo.model_validate(table),
        cart_id=cart.id,
        session_token=token,
    )


# --------------------------------------------------------------- Carrito helpers

def _load_open_cart(db: Session, session_id: UUID) -> Cart:
    cart = db.execute(
        select(Cart)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(Cart.session_id == session_id, Cart.status == "abierto")
    ).scalar_one_or_none()
    if cart is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay carrito abierto en la sesión")
    return cart


def _get_or_create_open_cart(db: Session, session_id: UUID) -> Cart:
    """Carrito 'abierto' vigente de la sesión, creándolo si no existe. Tras una
    consolidación el carrito anterior queda 'confirmado', así que el comensal
    reabre uno nuevo para seguir pidiendo (Fase 5). El índice parcial
    `idx_open_cart_per_session` garantiza uno solo abierto por sesión."""
    cart = db.execute(
        select(Cart)
        .options(selectinload(Cart.items).selectinload(CartItem.options))
        .where(Cart.session_id == session_id, Cart.status == "abierto")
    ).scalar_one_or_none()
    if cart is None:
        cart = Cart(session_id=session_id, status="abierto")
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


def serialize_cart(cart: Cart) -> CartResponse:
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
        id=cart.id, session_id=cart.session_id, status=cart.status,
        total=total, items=items,
    )


def get_cart(db: Session, session_id: UUID) -> CartResponse:
    return serialize_cart(_get_or_create_open_cart(db, session_id))


# --------------------------------------------------------------- CRUD de líneas

def add_item(db: Session, session_id: UUID, data: CartItemIn) -> CartResponse:
    cart = _get_or_create_open_cart(db, session_id)

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

    return get_cart(db, session_id)


def update_item(
    db: Session, session_id: UUID, item_id: UUID, data: CartItemUpdate
) -> CartResponse:
    cart = _load_open_cart(db, session_id)
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

    return get_cart(db, session_id)


def remove_item(db: Session, session_id: UUID, item_id: UUID) -> CartResponse:
    cart = _load_open_cart(db, session_id)
    item = next((i for i in cart.items if i.id == item_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Línea de carrito no encontrada")
    db.delete(item)
    db.commit()
    return get_cart(db, session_id)
