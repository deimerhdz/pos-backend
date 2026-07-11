"""Reservas de inventario (Fase 2): reservar al pedir, consumir al cobrar.

- `reserve_for_sale`: explota recetas, bloquea la fila del insumo (`FOR UPDATE`) y
  valida disponibilidad = Σ lotes vigentes − Σ reservas activas; crea reservas.
- `pay_order`: consume las reservas activas (FEFO, `consume_from_batches`) y las marca
  `consumed`.
- `release_reservations` / `release_expired`: liberan reservas sin mover inventario.
"""
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from app.core.units import convert
from app.core.exceptions import InsufficientStockError
from app.models.supply import Supply
from app.models.supply_batch import SupplyBatch
from app.models.stock_reservation import StockReservation
from app.models.unit_measure import UnitMeasure
from app.models.order import Order
from app.api.v1.supplies.consumption import resolve_recipe, consume_from_batches

RESERVATION_TTL_MIN = 30


def _explode(db: Session, lines) -> tuple[list[tuple[UUID, UUID, Decimal]], dict[UUID, Decimal]]:
    """lines: dicts {order_item_id, variant_id?, modifier_id?, quantity}.
    Devuelve (filas[(order_item_id, supply_id, qty_base)], total_por_insumo)."""
    rows: list[tuple[UUID, UUID, Decimal]] = []
    totals: dict[UUID, Decimal] = {}
    for ln in lines:
        recipe = resolve_recipe(db, variant_id=ln.get("variant_id"), modifier_id=ln.get("modifier_id"))
        if recipe is None:
            continue
        qty = Decimal(ln["quantity"])
        for item in recipe.items:
            supply = db.get(Supply, item.supply_id)
            unit = db.get(UnitMeasure, item.unit_measure_id)
            need = convert(item.quantity, unit, supply.unit_measure) * qty
            rows.append((ln["order_item_id"], supply.id, need))
            totals[supply.id] = totals.get(supply.id, Decimal(0)) + need
    return rows, totals


def _available_locked(db: Session, supply_id: UUID) -> Decimal:
    """Bloquea la fila del insumo (`FOR UPDATE`) y devuelve la disponibilidad =
    Σ lotes vigentes − Σ reservas activas. El lock serializa reservas concurrentes."""
    supply = db.execute(
        select(Supply).where(Supply.id == supply_id).with_for_update()
    ).scalar_one()

    batch_q = select(func.coalesce(func.sum(SupplyBatch.quantity), 0)).where(
        SupplyBatch.supply_id == supply_id,
        SupplyBatch.quantity > 0,
        SupplyBatch.active == True,
    )
    if supply.track_expiry:
        today = date.today()
        batch_q = batch_q.where(
            (SupplyBatch.expires_at.is_(None)) | (SupplyBatch.expires_at >= today)
        )
    batches = db.execute(batch_q).scalar_one()

    reserved = db.execute(
        select(func.coalesce(func.sum(StockReservation.quantity_reserved), 0)).where(
            StockReservation.supply_id == supply_id,
            StockReservation.status == "active",
        )
    ).scalar_one()

    return Decimal(batches) - Decimal(reserved)


def reserve_for_sale(db: Session, order: Order, lines) -> int:
    """Crea reservas para la orden. Lanza InsufficientStockError si algún insumo no
    tiene disponibilidad. No hace commit (dueño de la transacción = el service)."""
    rows, totals = _explode(db, lines)

    for supply_id, need in totals.items():
        if need <= 0:
            continue
        if _available_locked(db, supply_id) < need:
            supply = db.get(Supply, supply_id)
            raise InsufficientStockError(supply.name)

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_TTL_MIN)
    count = 0
    for order_item_id, supply_id, qty in rows:
        if qty <= 0:
            continue
        db.add(StockReservation(
            order_id=order.id, order_item_id=order_item_id, supply_id=supply_id,
            quantity_reserved=qty, status="active", expires_at=expires_at,
        ))
        count += 1
    return count


def pay_order(db: Session, order: Order) -> int:
    """Consume (FEFO) las reservas activas de la orden y las marca `consumed`.
    Devuelve nº de movimientos generados. No hace commit."""
    reservations = db.execute(
        select(StockReservation).where(
            StockReservation.order_id == order.id,
            StockReservation.status == "active",
        )
    ).scalars().all()

    movements = 0
    for r in reservations:
        mvs = consume_from_batches(
            db, r.supply_id, r.quantity_reserved,
            reference_type="order", reference_id=order.id,
        )
        movements += len(mvs)
        r.status = "consumed"
    return movements


def release_reservations(db: Session, order: Order) -> int:
    """Libera las reservas activas de una orden (sin mover inventario)."""
    result = db.execute(
        update(StockReservation)
        .where(StockReservation.order_id == order.id, StockReservation.status == "active")
        .values(status="released")
    )
    return result.rowcount or 0


def release_expired(db: Session) -> int:
    """Libera reservas activas vencidas de órdenes aún pendientes."""
    now = datetime.now(timezone.utc)
    pending_ids = select(Order.id).where(Order.status == "pending")
    result = db.execute(
        update(StockReservation)
        .where(
            StockReservation.status == "active",
            StockReservation.expires_at.is_not(None),
            StockReservation.expires_at < now,
            StockReservation.order_id.in_(pending_ids),
        )
        .values(status="released")
    )
    return result.rowcount or 0
