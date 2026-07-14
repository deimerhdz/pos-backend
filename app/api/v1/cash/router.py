from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.cash_register import CashRegister
from app.models.cash_shift import CashShift
from app.models.cash_movement import CashMovement
from app.models.cash_count_denomination import CashCountDenomination
from app.api.v1.cash import service
from app.api.v1.cash.schemas import (
    RegisterCreate, RegisterResponse,
    ShiftOpen, ShiftClose, ShiftResponse,
    CashMovementIn, CashMovementResponse,
    ReconciliationResponse,
)

router = APIRouter(prefix="/cash", tags=["cash"])


# ============================ Cajas ============================
@router.get("/registers", response_model=list[RegisterResponse], summary="Listar cajas")
def list_registers(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(CashRegister).order_by(CashRegister.name)).scalars().all()


@router.post("/registers", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED, summary="Crear caja")
def create_register(body: RegisterCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    ensure_unique(db, CashRegister, CashRegister.name, body.name, "Register name already exists")
    reg = CashRegister(name=body.name)
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg


# ============================ Turnos ============================
@router.post("/shifts/open", response_model=ShiftResponse, status_code=status.HTTP_201_CREATED, summary="Abrir turno de caja")
def open_shift(body: ShiftOpen, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_or_404(db, CashRegister, body.cash_register_id, "Register not found")
    shift = CashShift(
        cash_register_id=body.cash_register_id,
        user_id=user.id,
        user_name=user.name,
        opening_amount=body.opening_amount,
        status="open",
    )
    db.add(shift)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "La caja ya tiene un turno abierto")
    db.refresh(shift)
    return shift


@router.post("/shifts/{shift_id}/close", response_model=ShiftResponse, summary="Cerrar turno (arqueo)")
def close_shift(shift_id: UUID, body: ShiftClose, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    shift = get_or_404(db, CashShift, shift_id, "Shift not found")
    if shift.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "El turno ya está cerrado")

    counted = body.counted_amount
    if body.denominations:
        counted = sum((d.denomination * d.quantity for d in body.denominations), start=counted or 0)
        for d in body.denominations:
            db.add(CashCountDenomination(
                cash_shift_id=shift.id, denomination=d.denomination, quantity=d.quantity
            ))

    shift.counted_amount = counted
    shift.status = "closed"
    shift.closed_at = datetime.utcnow()
    db.commit()
    db.refresh(shift)
    return shift


@router.get("/shifts/{shift_id}/reconciliation", response_model=ReconciliationResponse, summary="Arqueo del turno")
def reconciliation(shift_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    shift = get_or_404(db, CashShift, shift_id, "Shift not found")
    return service.reconcile(db, shift)


# ============================ Movimientos de efectivo ============================
@router.post("/shifts/{shift_id}/movements", response_model=CashMovementResponse, status_code=status.HTTP_201_CREATED, summary="Registrar entrada/salida de efectivo")
def add_movement(shift_id: UUID, body: CashMovementIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    shift = get_or_404(db, CashShift, shift_id, "Shift not found")
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "El turno está cerrado")
    mov = CashMovement(
        cash_shift_id=shift.id, type=body.type.value, amount=body.amount,
        description=body.description, user_id=user.id,
    )
    db.add(mov)
    db.commit()
    db.refresh(mov)
    return mov
