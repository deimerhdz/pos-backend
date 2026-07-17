from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.core.models import User
from app.api.v1.invoices import service
from app.api.v1.invoices.schemas import InvoiceResponse

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("/{invoice_id}", response_model=InvoiceResponse, summary="Obtener una factura")
def get_invoice(invoice_id: UUID, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return service.get_invoice(db, invoice_id)


@router.get("", response_model=list[InvoiceResponse], summary="Listar facturas (filtros: table_id, order_id)")
def list_invoices(
    table_id: UUID | None = Query(None),
    order_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_invoices(db, table_id=table_id, order_id=order_id)
