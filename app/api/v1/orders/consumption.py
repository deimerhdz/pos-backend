"""Deducción de inventario al consolidar (Fase 4) — el pedido entra a cocina y
el stock se compromete. Espeja `deduct_sale` (sales/consumption.py) pero lee las
opciones de forma relacional (`OrderItemOption`/`Option`), no del JSONB de ventas.

El descuento es por ítem insertado (no snapshot al final), reutilizable por la
adición de un solo ítem de Fase 5."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recipe_item import RecipeItem
from app.models.option import Option
from app.models.order_item import OrderItem
from app.api.v1.inventory.stock import record_movement


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
            reason="Consumo de receta en consolidación", reference_type="order",
            reference_id=reference_id, user_id=user_id,
        )

    # (b) opciones elegidas con insumo ligado
    for option in options:
        if option.inventory_item_id is None or option.item_quantity <= 0:
            continue
        record_movement(
            db, option.inventory_item_id, type="out",
            quantity=option.item_quantity * qty,
            reason="Consumo de opción en consolidación", reference_type="order",
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
            reason="Reversa por anulación de ítem", reference_type="order_void",
            reference_id=reference_id, user_id=user_id,
        )

    for option in options:
        if option.inventory_item_id is None or option.item_quantity <= 0:
            continue
        record_movement(
            db, option.inventory_item_id, type="in",
            quantity=option.item_quantity * qty,
            reason="Reversa por anulación de ítem", reference_type="order_void",
            reference_id=reference_id, user_id=user_id,
        )
