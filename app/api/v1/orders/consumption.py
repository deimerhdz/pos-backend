"""Deducción de inventario al consolidar (Fase 4) — el pedido entra a cocina y
el stock se compromete. Espeja `deduct_sale` (sales/consumption.py) pero lee las
opciones de forma relacional (`OrderItemOption`/`Option`), no del JSONB de ventas.

El descuento es por ítem insertado (no snapshot al final), reutilizable por la
adición de un solo ítem de Fase 5."""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import inventory_reasons as reasons
from app.models.recipe_item import RecipeItem
from app.models.option import Option
from app.models.order_item import OrderItem
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.api.v1.inventory.stock import lock_items, record_movement
from app.api.v1.catalog.line_pricing import required_consumption


def _variant_label(db: Session, variant_id: UUID) -> str:
    """'Producto · Variante' para poder nombrar el problema en el mensaje."""
    row = db.execute(
        select(Product.name, ProductVariant.name)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id == variant_id)
    ).first()
    return f"{row[0]} · {row[1]}" if row else str(variant_id)


def ensure_consumes_inventory(
    db: Session, entries: list[tuple[OrderItem, list[Option]]]
) -> None:
    """Rechaza las líneas que no descontarían **nada**.

    Una variante sin receta (y sin opciones que liguen insumo) se vendería sin
    mover el stock: el kardex no registra nada y el inventario queda sobrestimado
    en silencio hasta el conteo físico. Es peor que un error, porque nadie se
    entera.

    Se comprueba aquí y no en cada endpoint porque este módulo es el paso común de
    los tres caminos que descuentan (confirmación del pedido, consolidación y
    adición de ítem por el mesero); validarlo en uno solo dejaría los otros
    abiertos, que es justo como se coló el problema.
    """
    faltan = [
        _variant_label(db, item.product_variant_id)
        for item, options in entries
        if not required_consumption(db, item.product_variant_id, item.quantity, options)
    ]
    if not faltan:
        return

    nombres = ", ".join(f"«{n}»" for n in dict.fromkeys(faltan))
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": (
                f"{nombres} no tiene receta configurada, así que venderlo no "
                "descontaría inventario. Cárgasela en Productos → Recetas para "
                "poder venderlo."
            ),
            "variantes_sin_receta": list(dict.fromkeys(faltan)),
        },
    )


def lock_consumption(
    db: Session, entries: list[tuple[OrderItem, list[Option]]]
) -> None:
    """Pre-bloquea, en orden canónico de id, todos los insumos que consumirán las
    líneas dadas. Debe llamarse **antes** de la primera mutación de stock para que
    los locks de la transacción se tomen siempre en el mismo orden (ver
    `stock.lock_items`)."""
    needed: set[UUID] = set()
    for item, options in entries:
        needed |= set(
            required_consumption(db, item.product_variant_id, item.quantity, options)
        )
    lock_items(db, needed)


def deduct_order_items(
    db: Session,
    entries: list[tuple[OrderItem, list[Option]]],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Descuenta el inventario de varias líneas de una vez, tomando los locks en
    orden fijo primero. Es la vía preferida cuando se insertan N líneas en la
    misma transacción (consolidación, confirmación de pedido).

    Rechaza el lote entero si alguna línea no consumiría nada: mejor parar y
    configurar la receta que emitir una venta que descuadra el inventario."""
    ensure_consumes_inventory(db, entries)
    lock_consumption(db, entries)
    for item, options in entries:
        deduct_order_item(db, item, options, user_id, reference_id)


def reverse_order_items(
    db: Session,
    entries: list[tuple[OrderItem, list[Option]]],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Reversa en bloque (cancelación de orden). Aunque los 'in' nunca fallan por
    stock, siguen tomando locks: se piden en orden fijo por la misma razón que en
    `deduct_order_items`."""
    lock_consumption(db, entries)
    for item, options in entries:
        reverse_order_item(db, item, options, user_id, reference_id)


def deduct_order_item(
    db: Session,
    order_item: OrderItem,
    options: list[Option],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Descuenta el inventario que consume una línea de orden: (a) insumos de la
    receta de la variante y (b) insumos de las opciones elegidas. Escribe kardex
    'out' con lock; `InsufficientStockError` (400) propaga y fuerza rollback."""
    qty = Decimal(order_item.quantity)

    # (a) receta de la variante
    recipe = db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == order_item.product_variant_id)
    ).scalars().all()
    for ri in recipe:
        record_movement(
            db, ri.inventory_item_id, type="out", quantity=ri.quantity * qty,
            reason=reasons.VENTA, reference_type=reasons.REF_ORDER,
            reference_id=reference_id, user_id=user_id,
        )

    # (b) opciones elegidas con insumo ligado
    for option in options:
        if option.inventory_item_id is None or option.item_quantity <= 0:
            continue
        record_movement(
            db, option.inventory_item_id, type="out",
            quantity=option.item_quantity * qty,
            reason=reasons.VENTA, reference_type=reasons.REF_ORDER,
            reference_id=reference_id, user_id=user_id,
        )


def reverse_order_item(
    db: Session,
    order_item: OrderItem,
    options: list[Option],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Inverso de `deduct_order_item`: devuelve al stock lo que consumía una línea
    (receta + opciones). Movimientos 'in' (siempre suman, no lanzan
    InsufficientStockError). Se usa al anular un ítem 'pendiente' (cocina no lo
    consumió) y en la cancelación de orden (Fase 7)."""
    qty = Decimal(order_item.quantity)

    recipe = db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == order_item.product_variant_id)
    ).scalars().all()
    for ri in recipe:
        record_movement(
            db, ri.inventory_item_id, type="in", quantity=ri.quantity * qty,
            reason=reasons.AJUSTE, reference_type=reasons.REF_ORDER_VOID,
            reference_id=reference_id, user_id=user_id,
        )

    for option in options:
        if option.inventory_item_id is None or option.item_quantity <= 0:
            continue
        record_movement(
            db, option.inventory_item_id, type="in",
            quantity=option.item_quantity * qty,
            reason=reasons.AJUSTE, reference_type=reasons.REF_ORDER_VOID,
            reference_id=reference_id, user_id=user_id,
        )
