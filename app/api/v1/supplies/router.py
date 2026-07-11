from uuid import UUID
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.supply import Supply
from app.models.supply_batch import SupplyBatch
from app.models.supply_movement import SupplyMovement
from app.models.unit_measure import UnitMeasure
from app.api.v1.supplies.consumption import consume_sale
from app.api.v1.supplies.schemas import (
    SupplyCreate,
    SupplyUpdate,
    SupplyResponse,
    SupplyBatchCreate,
    SupplyBatchResponse,
    SupplyMovementCreate,
    SupplyMovementResponse,
    AlertsResponse,
    ConsumeRequest,
    ConsumeResponse,
)

router = APIRouter(prefix="/supplies", tags=["supplies"])


@router.get("", response_model=list[SupplyResponse], summary="Listar insumos")
def list_supplies(
    active: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Supply).order_by(Supply.name)
    if active is not None:
        stmt = stmt.where(Supply.active == active)
    return db.execute(stmt).scalars().all()


@router.get("/alerts", response_model=AlertsResponse, summary="Insumos bajo mínimo y lotes por vencer")
def alerts(
    days: int = Query(15, ge=0, description="Ventana de días para 'por vencer'."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    low = db.execute(
        select(Supply).where(Supply.active == True, Supply.stock_current < Supply.stock_min)
    ).scalars().all()

    horizon = date.today() + timedelta(days=days)
    expiring = db.execute(
        select(SupplyBatch)
        .options(selectinload(SupplyBatch.supply))
        .where(
            SupplyBatch.quantity > 0,
            SupplyBatch.expires_at.is_not(None),
            SupplyBatch.expires_at <= horizon,
        )
        .order_by(SupplyBatch.expires_at)
    ).scalars().all()

    return {
        "low_stock": [
            {"supply_id": s.id, "name": s.name, "stock_current": s.stock_current, "stock_min": s.stock_min}
            for s in low
        ],
        "expiring": [
            {
                "supply_id": b.supply_id, "supply_name": b.supply.name if b.supply else None,
                "batch_id": b.id, "code": b.code, "quantity": b.quantity, "expires_at": b.expires_at,
            }
            for b in expiring
        ],
    }


@router.post("/consume", response_model=ConsumeResponse, summary="Consumir insumos por receta (motor FEFO)")
def consume(
    body: ConsumeRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    result = consume_sale(db, body.lines, reference_id=body.reference_id)
    db.commit()
    return result


@router.get("/{id}", response_model=SupplyResponse, summary="Obtener un insumo")
def get_supply(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, Supply, id, "Supply not found")


@router.post("", response_model=SupplyResponse, status_code=status.HTTP_201_CREATED, summary="Crear un insumo")
def create_supply(
    body: SupplyCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, UnitMeasure, body.unit_measure_id, "Unit measure not found")
    supply = Supply(
        name=body.name,
        unit_measure_id=body.unit_measure_id,
        stock_min=body.stock_min,
        track_expiry=body.track_expiry,
    )
    db.add(supply)
    db.commit()
    db.refresh(supply)
    return supply


@router.patch("/{id}", response_model=SupplyResponse, summary="Actualizar un insumo")
def update_supply(
    id: UUID,
    body: SupplyUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    supply = get_or_404(db, Supply, id, "Supply not found")
    if body.name is not None:
        supply.name = body.name
    if body.stock_min is not None:
        supply.stock_min = body.stock_min
    if body.track_expiry is not None:
        supply.track_expiry = body.track_expiry
    if body.active is not None:
        supply.active = body.active
    db.commit()
    db.refresh(supply)
    return supply


@router.get("/{id}/batches", response_model=list[SupplyBatchResponse], summary="Listar lotes de un insumo")
def list_batches(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, Supply, id, "Supply not found")
    return db.execute(
        select(SupplyBatch).where(SupplyBatch.supply_id == id).order_by(SupplyBatch.expires_at)
    ).scalars().all()


@router.post("/{id}/batches", response_model=SupplyBatchResponse, status_code=status.HTTP_201_CREATED, summary="Registrar entrada de lote")
def register_batch(
    id: UUID,
    body: SupplyBatchCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    supply = get_or_404(db, Supply, id, "Supply not found")
    if supply.track_expiry and body.expires_at is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Este insumo controla vencimiento: el lote requiere expires_at.",
        )

    batch = SupplyBatch(
        supply_id=id,
        code=body.code,
        quantity=body.quantity,
        expires_at=body.expires_at,
        unit_cost=body.unit_cost,
        received_at=body.received_at or date.today(),
    )
    db.add(batch)
    db.flush()

    supply.stock_current = supply.stock_current + body.quantity
    db.add(SupplyMovement(
        supply_id=id, batch_id=batch.id, quantity=body.quantity,
        type="income", reason="Entrada de lote",
    ))

    db.commit()
    db.refresh(batch)
    return batch


@router.post("/{id}/movements", response_model=SupplyMovementResponse, status_code=status.HTTP_201_CREATED, summary="Ajuste o merma de un insumo")
def register_movement(
    id: UUID,
    body: SupplyMovementCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    supply = get_or_404(db, Supply, id, "Supply not found")

    if body.type == "waste":
        if body.quantity > supply.stock_current:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Insufficient stock for waste")
        supply.stock_current = supply.stock_current - body.quantity
    else:  # adjust: delta con signo
        supply.stock_current = supply.stock_current + body.quantity

    movement = SupplyMovement(
        supply_id=id, quantity=abs(body.quantity), type=body.type, reason=body.reason,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement
