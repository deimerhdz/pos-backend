"""Disponibilidad calculada (Fase 3): stock real vs flag `active`.

`is_available` es en tiempo real: una variante está disponible si todos los insumos
de su receta tienen `Σ lotes vigentes − Σ reservas activas ≥` lo que consume 1 unidad.
Es solo lectura (sin locks); el gate transaccional real es la reserva (Fase 2).
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.units import convert
from app.models.supply import Supply
from app.models.supply_batch import SupplyBatch
from app.models.stock_reservation import StockReservation
from app.models.unit_measure import UnitMeasure
from app.models.variant import Variant
from app.api.v1.supplies.consumption import resolve_recipe


def supply_available(db: Session, supply_id: UUID) -> Decimal:
    supply = db.get(Supply, supply_id)
    bq = select(func.coalesce(func.sum(SupplyBatch.quantity), 0)).where(
        SupplyBatch.supply_id == supply_id,
        SupplyBatch.quantity > 0,
        SupplyBatch.active == True,
    )
    if supply is not None and supply.track_expiry:
        today = date.today()
        bq = bq.where((SupplyBatch.expires_at.is_(None)) | (SupplyBatch.expires_at >= today))
    batches = db.execute(bq).scalar_one()
    reserved = db.execute(
        select(func.coalesce(func.sum(StockReservation.quantity_reserved), 0)).where(
            StockReservation.supply_id == supply_id,
            StockReservation.status == "active",
        )
    ).scalar_one()
    return Decimal(batches) - Decimal(reserved)


def variant_is_available(db: Session, variant: Variant) -> bool:
    recipe = resolve_recipe(db, variant_id=variant.id)
    if recipe is None or not recipe.items:
        return False
    for item in recipe.items:
        supply = db.get(Supply, item.supply_id)
        unit = db.get(UnitMeasure, item.unit_measure_id)
        need = convert(item.quantity, unit, supply.unit_measure)  # por 1 unidad vendida
        if supply_available(db, supply.id) < need:
            return False
    return True


def product_is_available(db: Session, product) -> bool:
    variants = db.execute(
        select(Variant).where(Variant.product_id == product.id, Variant.active == True)
    ).scalars().all()
    return any(variant_is_available(db, v) for v in variants)
