"""Motor de consumo de insumos por receta (FEFO + vencimiento).

Explota la receta de cada línea (variante o modificador), convierte a la unidad
base del insumo, y descuenta por lote FEFO registrando movimientos de salida.
Bloquea si un insumo no tiene stock no vencido suficiente.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.units import convert
from app.models.recipe import Recipe
from app.models.supply import Supply
from app.models.supply_batch import SupplyBatch
from app.models.supply_movement import SupplyMovement
from app.models.unit_measure import UnitMeasure


def resolve_recipe(db: Session, *, variant_id: UUID | None = None, modifier_id: UUID | None = None) -> Recipe | None:
    stmt = select(Recipe).options(selectinload(Recipe.items)).where(Recipe.active == True)
    if variant_id is not None:
        stmt = stmt.where(Recipe.variant_id == variant_id)
    else:
        stmt = stmt.where(Recipe.modifier_id == modifier_id)
    return db.execute(stmt).scalar_one_or_none()


def consume_sale(db: Session, lines, reference_id: UUID | None = None) -> dict:
    """Calcula el consumo agregado por insumo y lo descuenta FEFO. No hace commit
    (el endpoint es dueño de la transacción)."""
    # 1) Agregar lo necesario por insumo, en su unidad base.
    needed: dict[UUID, Decimal] = {}
    for line in lines:
        qty = Decimal(line.quantity)
        recipe = resolve_recipe(db, variant_id=line.variant_id, modifier_id=line.modifier_id)
        if recipe is None:
            continue  # sin receta = no consume (producto sin receta)
        for item in recipe.items:
            supply = db.get(Supply, item.supply_id)
            item_unit = db.get(UnitMeasure, item.unit_measure_id)
            base_qty = convert(item.quantity, item_unit, supply.unit_measure) * qty
            needed[supply.id] = needed.get(supply.id, Decimal(0)) + base_qty

    # 2) Descontar FEFO por insumo.
    today = date.today()
    consumed: list[dict] = []
    movements = 0

    for supply_id, need in needed.items():
        if need <= 0:
            continue
        supply = db.get(Supply, supply_id)
        batches = db.execute(
            select(SupplyBatch).where(
                SupplyBatch.supply_id == supply_id, SupplyBatch.quantity > 0
            )
        ).scalars().all()
        if supply.track_expiry:
            batches = [b for b in batches if b.expires_at is None or b.expires_at >= today]
        # FEFO: primero el que vence antes (NULL al final), luego por recepción.
        batches.sort(key=lambda b: (b.expires_at is None, b.expires_at or today, b.received_at))

        available = sum((b.quantity for b in batches), Decimal(0))
        if available < need:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stock insuficiente o vencido para el insumo '{supply.name}'.",
            )

        remaining = need
        for b in batches:
            if remaining <= 0:
                break
            take = b.quantity if b.quantity < remaining else remaining
            b.quantity = b.quantity - take
            remaining -= take
            db.add(SupplyMovement(
                supply_id=supply_id, batch_id=b.id, quantity=take,
                type="expense", reference_id=reference_id, reason="Consumo",
            ))
            movements += 1

        supply.stock_current = supply.stock_current - need
        consumed.append({
            "supply_id": supply.id,
            "name": supply.name,
            "consumed": need,
            "unit": supply.unit_measure.abbreviation if supply.unit_measure else None,
            "stock_after": supply.stock_current,
            "below_min": supply.stock_current < supply.stock_min,
        })

    return {
        "consumed": consumed,
        "movements": movements,
        "alerts": [c for c in consumed if c["below_min"]],
    }
