from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.payment import PaymentMethod
from app.models.sale import Sale
from app.api.v1.sales import service
from app.api.v1.sales.schemas import (
    PaymentMethodCreate, PaymentMethodResponse,
    SaleCreate, SaleResponse,
)

router = APIRouter(prefix="/sales", tags=["sales"])


# ============================ Métodos de pago ============================
@router.get("/payment-methods", response_model=list[PaymentMethodResponse], summary="Listar métodos de pago")
def list_payment_methods(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(select(PaymentMethod).order_by(PaymentMethod.name)).scalars().all()


@router.post("/payment-methods", response_model=PaymentMethodResponse, status_code=status.HTTP_201_CREATED, summary="Crear método de pago")
def create_payment_method(body: PaymentMethodCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    ensure_unique(db, PaymentMethod, PaymentMethod.name, body.name, "Payment method already exists")
    # Mantener is_cash y type consistentes (is_cash ⇔ type == 'cash').
    if body.type is not None:
        method_type = body.type.value
    else:
        method_type = "cash" if body.is_cash else "other"
    pm = PaymentMethod(name=body.name, type=method_type, is_cash=(method_type == "cash"))
    db.add(pm)
    db.commit()
    db.refresh(pm)
    return pm


# ============================ Ventas ============================
@router.post("", response_model=SaleResponse, status_code=status.HTTP_201_CREATED, summary="Checkout: emitir y cobrar una venta")
def create_sale(body: SaleCreate, db: Session = Depends(get_db), cashier: User = Depends(get_current_user)):
    sale = service.checkout(db, body, cashier)
    return _load_sale(db, sale.id)


@router.get("", response_model=list[SaleResponse], summary="Listar ventas")
def list_sales(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .order_by(Sale.sold_at.desc())
    ).scalars().all()


@router.get("/{sale_id}", response_model=SaleResponse, summary="Obtener una venta")
def get_sale(sale_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _load_sale(db, sale_id)


def _load_sale(db: Session, sale_id: UUID) -> Sale:
    sale = db.execute(
        select(Sale)
        .options(selectinload(Sale.items), selectinload(Sale.payments))
        .where(Sale.id == sale_id)
    ).scalar_one_or_none()
    if sale is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sale not found")
    return sale
