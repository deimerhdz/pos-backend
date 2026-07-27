"""Reportes gerenciales (RF-059..065). Solo lectura; requiere admin del tenant."""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import require_tenant_admin
from app.core.models import User
from app.api.v1.reports import service
from app.api.v1.reports.schemas import (
    SalesReport, ProductRow, CategoryRow, CashierRow, InventoryRow, ProfitabilityReport,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sales", response_model=SalesReport, summary="Reporte de ventas (RF-059)")
def sales(
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.sales_report(db, date_from, date_to)


@router.get("/products", response_model=list[ProductRow], summary="Ventas por producto (RF-060)")
def products(
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.products_report(db, date_from, date_to, limit=None)


@router.get("/top-products", response_model=list[ProductRow], summary="Productos más vendidos (RF-064)")
def top_products(
    limit: int = Query(10, ge=1, le=100),
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.products_report(db, date_from, date_to, limit=limit)


@router.get("/categories", response_model=list[CategoryRow], summary="Ventas por categoría (RF-061)")
def categories(
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.categories_report(db, date_from, date_to)


@router.get("/cashiers", response_model=list[CashierRow], summary="Ventas por cajero (RF-062)")
def cashiers(
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.cashiers_report(db, date_from, date_to)


@router.get("/inventory", response_model=list[InventoryRow], summary="Reporte de inventario (RF-063)")
def inventory(db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    return service.inventory_report(db)


@router.get("/profitability", response_model=ProfitabilityReport, summary="Rentabilidad (RF-065)")
def profitability(
    date_from: date | None = Query(None), date_to: date | None = Query(None),
    db: Session = Depends(get_db), _: User = Depends(require_tenant_admin),
):
    return service.profitability_report(db, date_from, date_to)
