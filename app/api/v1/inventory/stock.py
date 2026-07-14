"""Motor de stock único (sin lotes). Toda variación pasa por `record_movement`,
que escribe el kardex (`inventory_movements`) y ajusta `current_stock` de forma
atómica bajo `SELECT ... FOR UPDATE`.
"""
from decimal import Decimal
from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientStockError
from app.models.inventory_item import InventoryItem
from app.models.inventory_movement import InventoryMovement


def record_movement(
    db: Session,
    inventory_item_id: UUID,
    *,
    type: str,
    quantity: Decimal,
    reason: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    allow_negative: bool = False,
) -> InventoryMovement:
    """Aplica un movimiento de inventario. `quantity` es siempre > 0; el signo lo
    determina `type`: 'in' suma, 'out' resta, 'adjustment' sigue `signed_delta`
    (usa `apply_adjustment` para eso). Bloquea la fila del insumo para evitar
    condiciones de carrera."""
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    item = db.execute(
        select(InventoryItem).where(InventoryItem.id == inventory_item_id).with_for_update()
    ).scalar_one()

    delta = quantity if type == "in" else -quantity
    new_stock = item.current_stock + delta
    if new_stock < 0 and not allow_negative:
        raise InsufficientStockError(
            f"Stock insuficiente para '{item.name}': disponible {item.current_stock}, requerido {quantity}"
        )
    item.current_stock = new_stock

    movement = InventoryMovement(
        inventory_item_id=inventory_item_id,
        type=type,
        quantity=quantity,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        user_id=user_id,
    )
    db.add(movement)
    return movement


def apply_adjustment(
    db: Session,
    inventory_item_id: UUID,
    *,
    signed_delta: Decimal,
    reason: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> InventoryMovement:
    """Ajuste manual de stock (delta con signo). Registra un movimiento
    'adjustment' con la magnitud y mueve el stock en la dirección del delta."""
    if signed_delta == 0:
        raise ValueError("signed_delta must be != 0")
    item = db.execute(
        select(InventoryItem).where(InventoryItem.id == inventory_item_id).with_for_update()
    ).scalar_one()
    new_stock = item.current_stock + signed_delta
    if new_stock < 0:
        raise InsufficientStockError(
            f"Ajuste dejaría el stock de '{item.name}' negativo"
        )
    item.current_stock = new_stock
    movement = InventoryMovement(
        inventory_item_id=inventory_item_id,
        type="adjustment",
        quantity=abs(signed_delta),
        reason=reason,
    )
    movement.user_id = user_id
    db.add(movement)
    return movement
