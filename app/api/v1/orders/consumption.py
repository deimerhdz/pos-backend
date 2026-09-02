"""Deducción de inventario al consolidar (Fase 4) — el pedido entra a cocina y
el stock se compromete. Espeja `deduct_sale` (sales/consumption.py) pero lee las
opciones de forma relacional (`OrderItemOption`/`Option`), no del JSONB de ventas.

El descuento es por ítem insertado (no snapshot al final), reutilizable por la
adición de un solo ítem de Fase 5.

Qué consume cada línea lo decide `catalog/consumption_plan.py`, compartido con la
venta de mostrador: aquí solo se elige la dirección del movimiento ('out' al
descontar, 'in' al revertir)."""
from uuid import UUID

from sqlalchemy.orm import Session

from app.core import inventory_reasons as reasons
from app.catalog_engine import ChosenOption
from app.models.order_item import OrderItem
from app.api.v1.inventory.stock import lock_items, record_movement
from app.api.v1.catalog.consumption_plan import (
    ensure_lines_consume_inventory,
    plan_line_consumption,
    required_consumption,
)


def ensure_consumes_inventory(
    db: Session, entries: list[tuple[OrderItem, list[ChosenOption]]]
) -> None:
    """Rechaza las líneas que no descontarían **nada**. Adaptador de
    `ensure_lines_consume_inventory` a las líneas de orden; la venta de mostrador usa
    el mismo núcleo desde `sales/consumption.py`."""
    ensure_lines_consume_inventory(
        db, [(item.product_variant_id, item.quantity, options) for item, options in entries]
    )


def lock_consumption(
    db: Session, entries: list[tuple[OrderItem, list[ChosenOption]]]
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
    entries: list[tuple[OrderItem, list[ChosenOption]]],
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
    entries: list[tuple[OrderItem, list[ChosenOption]]],
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
    options: list[ChosenOption],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Descuenta el inventario que consume una línea de orden (ver
    `plan_line_consumption`: receta fija + slots resueltos por las opciones elegidas).
    Escribe kardex 'out' con lock; `InsufficientStockError` (400) propaga y fuerza
    rollback."""
    for line in plan_line_consumption(
        db, order_item.product_variant_id, order_item.quantity, options
    ):
        record_movement(
            db, line.inventory_item_id, type="out", quantity=line.quantity,
            reason=reasons.VENTA, reference_type=reasons.REF_ORDER,
            reference_id=reference_id, user_id=user_id,
        )


def reverse_order_item(
    db: Session,
    order_item: OrderItem,
    options: list[ChosenOption],
    user_id: UUID | None,
    reference_id: UUID,
) -> None:
    """Inverso exacto de `deduct_order_item`: devuelve al stock lo que consumía una
    línea. Movimientos 'in' (siempre suman, no lanzan InsufficientStockError). Se usa
    al anular un ítem 'pendiente' (cocina no lo consumió) y en la cancelación de
    orden (Fase 7).

    Comparte `plan_line_consumption` con el descuento a propósito: una reversa que
    calcule distinto descuadra el inventario para siempre, porque nadie concilia los
    'in' contra los 'out'."""
    for line in plan_line_consumption(
        db, order_item.product_variant_id, order_item.quantity, options
    ):
        record_movement(
            db, line.inventory_item_id, type="in", quantity=line.quantity,
            reason=reasons.AJUSTE, reference_type=reasons.REF_ORDER_VOID,
            reference_id=reference_id, user_id=user_id,
        )
