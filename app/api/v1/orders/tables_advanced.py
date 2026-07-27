"""Operaciones avanzadas de mesas (RF-051..054): estados de mesa, cambio de
mesa de una orden, y unión de mesas por grupo. La división de cuenta por comensal
(RF-054) ya la expone `compute_bill` (líneas por `session_id`) en el bill de mesa."""
import uuid
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import IntegrityError

from app.core.crud import get_or_404
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.order_item import OrderItem

TERMINAL = ("pagada", "cancelada")


def _active_orders_on_table(db: Session, table_id: UUID) -> list[CustomerOrder]:
    return db.execute(
        select(CustomerOrder).where(
            CustomerOrder.dining_table_id == table_id,
            CustomerOrder.status.notin_(TERMINAL),
        )
    ).scalars().all()


def set_table_status(db: Session, table_id: UUID, new_status: str) -> DiningTable:
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    # 'libre'/'reservada' sólo si la mesa no tiene órdenes activas.
    if new_status in ("libre", "reservada") and _active_orders_on_table(db, table.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La mesa tiene órdenes sin cerrar; no se puede marcar como "
            f"'{new_status}'.",
        )
    table.status = new_status
    db.commit()
    db.refresh(table)
    return table


def move_order(db: Session, order_id: UUID, target_table_id: UUID) -> CustomerOrder:
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status in TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "La orden ya está cerrada")

    target = get_or_404(db, DiningTable, target_table_id, "Target table not found")
    if target.id == order.dining_table_id:
        return order
    if _active_orders_on_table(db, target.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa destino ya tiene una orden activa")

    source_id = order.dining_table_id
    order.dining_table_id = target.id
    target.status = "ocupada"
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "La mesa destino ya tiene una orden abierta")

    # Liberar la mesa origen si ya no tiene órdenes activas.
    if source_id and not _active_orders_on_table(db, source_id):
        src = db.get(DiningTable, source_id)
        if src is not None:
            src.status = "libre"
    db.commit()
    db.refresh(order)
    return order


def merge_orders(db: Session, order_ids: list[UUID]) -> dict:
    orders = db.execute(
        select(CustomerOrder).where(CustomerOrder.id.in_(order_ids))
    ).scalars().all()
    if len(orders) != len(set(order_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alguna orden no existe")
    if any(o.status in TERMINAL for o in orders):
        raise HTTPException(status.HTTP_409_CONFLICT, "No se pueden unir órdenes ya cerradas")

    # Reusar un grupo existente entre las órdenes, o crear uno nuevo.
    group_id = next((o.merged_group_id for o in orders if o.merged_group_id), uuid.uuid4())
    for o in orders:
        o.merged_group_id = group_id
    db.commit()
    return {"merged_group_id": group_id, "order_ids": [o.id for o in orders]}


def group_bill(db: Session, group_id: UUID) -> dict:
    orders = db.execute(
        select(CustomerOrder)
        .options(selectinload(CustomerOrder.items))
        .where(CustomerOrder.merged_group_id == group_id)
    ).scalars().all()
    if not orders:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grupo de mesas no encontrado")

    per_order = []
    total = Decimal("0")
    for o in orders:
        sub = sum(
            (Decimal(it.unit_price) * it.quantity
             for it in o.items if it.estado_cocina != "anulado"),
            start=Decimal("0"),
        )
        total += sub
        per_order.append({
            "order_id": o.id, "dining_table_id": o.dining_table_id,
            "status": o.status, "subtotal": sub,
        })
    return {"merged_group_id": group_id, "total": total, "orders": per_order}
