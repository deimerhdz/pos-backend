"""Motor de consumo de insumos por receta (FEFO + vencimiento), atómico.

`consume_from_batches` es la ÚNICA función que escribe `SupplyMovement` de salida
('expense'). Bloquea las filas de lote (`SELECT ... FOR UPDATE`) ordenadas por
vencimiento para que dos consumos concurrentes no sobrevendan el mismo lote.
`consume_sale` explota las recetas de las líneas de venta, agrega la necesidad por
insumo y delega en `consume_from_batches`.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.units import convert
from app.core.exceptions import InsufficientStockError
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


def consume_from_batches(
    db: Session,
    supply_id: UUID,
    quantity_needed: Decimal,
    *,
    reference_type: str = "order",
    reference_id: UUID | None = None,
) -> list[SupplyMovement]:
    """Descuenta `quantity_needed` (en la unidad base del insumo) de sus lotes por
    FEFO, con bloqueo de filas. Atómico: si no alcanza el stock vigente, no descuenta
    nada y lanza `InsufficientStockError`. Única vía de escritura de movimientos 'expense'."""
    supply = db.get(Supply, supply_id)
    if supply is None or quantity_needed <= 0:
        return []

    stmt = (
        select(SupplyBatch)
        .where(
            SupplyBatch.supply_id == supply_id,
            SupplyBatch.quantity > 0,
            SupplyBatch.active == True,
        )
        .order_by(SupplyBatch.expires_at.asc().nullslast(), SupplyBatch.received_at.asc())
        .with_for_update()
    )
    batches = db.execute(stmt).scalars().all()
    if supply.track_expiry:
        today = date.today()
        batches = [b for b in batches if b.expires_at is None or b.expires_at >= today]

    available = sum((b.quantity for b in batches), Decimal(0))
    if available < quantity_needed:
        raise InsufficientStockError(supply.name)

    movements: list[SupplyMovement] = []
    remaining = quantity_needed
    for b in batches:
        if remaining <= 0:
            break
        take = b.quantity if b.quantity < remaining else remaining
        b.quantity = b.quantity - take
        remaining -= take
        mv = SupplyMovement(
            supply_id=supply_id, batch_id=b.id, quantity=take, type="expense",
            reference_id=reference_id, reason=f"Consumo ({reference_type})",
        )
        db.add(mv)
        movements.append(mv)

    supply.stock_current = supply.stock_current - quantity_needed
    return movements


def consume_sale(db: Session, lines, reference_id: UUID | None = None) -> dict:
    """Explota recetas de las líneas (variante/modificador), agrega necesidad por
    insumo (en unidad base) y descuenta con `consume_from_batches`. No hace commit."""
    needed: dict[UUID, Decimal] = {}
    for line in lines:
        qty = Decimal(line.quantity)
        recipe = resolve_recipe(db, variant_id=line.variant_id, modifier_id=line.modifier_id)
        if recipe is None:
            continue
        for item in recipe.items:
            supply = db.get(Supply, item.supply_id)
            item_unit = db.get(UnitMeasure, item.unit_measure_id)
            base_qty = convert(item.quantity, item_unit, supply.unit_measure) * qty
            needed[supply.id] = needed.get(supply.id, Decimal(0)) + base_qty

    consumed: list[dict] = []
    movements = 0
    for supply_id, need in needed.items():
        if need <= 0:
            continue
        mvs = consume_from_batches(
            db, supply_id, need, reference_type="order", reference_id=reference_id
        )
        movements += len(mvs)
        supply = db.get(Supply, supply_id)
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
