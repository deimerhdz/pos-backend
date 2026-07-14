"""Lógica de caja: resolución del turno abierto y arqueo (reconciliación),
equivalente a la vista `v_shift_reconciliation` de schema.sql en la capa de app."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.cash_shift import CashShift
from app.models.cash_movement import CashMovement
from app.models.sale import Sale
from app.models.payment import Payment, PaymentMethod


def get_open_shift(db: Session, cash_register_id: UUID) -> CashShift | None:
    return db.execute(
        select(CashShift).where(
            CashShift.cash_register_id == cash_register_id,
            CashShift.status == "open",
        )
    ).scalar_one_or_none()


def reconcile(db: Session, shift: CashShift) -> dict:
    cash_sales = db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .select_from(Sale)
        .join(Payment, Payment.sale_id == Sale.id)
        .join(PaymentMethod, PaymentMethod.id == Payment.payment_method_id)
        .where(
            Sale.cash_shift_id == shift.id,
            Sale.status == "paid",
            PaymentMethod.is_cash.is_(True),
        )
    ).scalar_one()

    cash_in = db.execute(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.cash_shift_id == shift.id, CashMovement.type == "in"
        )
    ).scalar_one()
    cash_out = db.execute(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.cash_shift_id == shift.id, CashMovement.type == "out"
        )
    ).scalar_one()

    expected = Decimal(shift.opening_amount) + Decimal(cash_sales) + Decimal(cash_in) - Decimal(cash_out)
    difference = None
    if shift.counted_amount is not None:
        difference = Decimal(shift.counted_amount) - expected

    return {
        "cash_shift_id": shift.id,
        "status": shift.status,
        "opening_amount": Decimal(shift.opening_amount),
        "cash_sales": Decimal(cash_sales),
        "cash_in": Decimal(cash_in),
        "cash_out": Decimal(cash_out),
        "expected": expected,
        "counted_amount": shift.counted_amount,
        "difference": difference,
    }
