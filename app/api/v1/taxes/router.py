from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.tax import Tax
from app.models.tax_link import TaxLink
from app.models.product import Product
from app.models.variant import Variant
from app.api.v1.taxes.schemas import (
    TaxCreate,
    TaxUpdate,
    TaxResponse,
    TaxLinkCreate,
    TaxLinkResponse,
)

router = APIRouter(prefix="/taxes", tags=["taxes"])


@router.get("", response_model=list[TaxResponse], summary="Listar impuestos")
def list_taxes(
    active: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Tax).order_by(Tax.name)
    if active is not None:
        stmt = stmt.where(Tax.active == active)
    return db.execute(stmt).scalars().all()


@router.get("/{id}", response_model=TaxResponse, summary="Obtener un impuesto")
def get_tax(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return get_or_404(db, Tax, id, "Tax not found")


@router.post("", response_model=TaxResponse, status_code=status.HTTP_201_CREATED, summary="Crear un impuesto")
def create_tax(
    body: TaxCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    tax = Tax(name=body.name, rate=body.rate, inclusive=body.inclusive)
    db.add(tax)
    db.commit()
    db.refresh(tax)
    return tax


@router.patch("/{id}", response_model=TaxResponse, summary="Actualizar un impuesto")
def update_tax(
    id: UUID,
    body: TaxUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    tax = get_or_404(db, Tax, id, "Tax not found")
    if body.name is not None:
        tax.name = body.name
    if body.rate is not None:
        tax.rate = body.rate
    if body.inclusive is not None:
        tax.inclusive = body.inclusive
    if body.active is not None:
        tax.active = body.active
    db.commit()
    db.refresh(tax)
    return tax


@router.post("/links", response_model=TaxLinkResponse, status_code=status.HTTP_201_CREATED, summary="Asociar un impuesto a un producto o variante")
def create_tax_link(
    body: TaxLinkCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Tax, body.tax_id, "Tax not found")
    if body.product_id is not None:
        get_or_404(db, Product, body.product_id, "Product not found")
    if body.variant_id is not None:
        get_or_404(db, Variant, body.variant_id, "Variant not found")

    link = TaxLink(tax_id=body.tax_id, product_id=body.product_id, variant_id=body.variant_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
