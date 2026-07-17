"""Facturación (Fase 8): genera una factura interna (snapshot inmutable) por
cada order pagada. Consecutivo serializado con lock. DIAN fuera de v1."""
import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.core.models import User
from app.models.dining_table import DiningTable
from app.models.customer_order import CustomerOrder
from app.models.sale import Sale, SaleItem
from app.models.invoice import Invoice, InvoiceCounter
from app.api.v1.invoices.schemas import InvoiceItemResponse, InvoiceResponse

logger = logging.getLogger(__name__)


def _next_number(db: Session, prefix: str) -> int:
    """Asigna el siguiente consecutivo del prefijo, bloqueando la fila del
    contador (get-or-create)."""
    counter = db.execute(
        select(InvoiceCounter).where(InvoiceCounter.prefix == prefix).with_for_update()
    ).scalar_one_or_none()
    if counter is None:
        counter = InvoiceCounter(prefix=prefix, next_number=1)
        db.add(counter)
        db.flush()
    n = counter.next_number
    counter.next_number = n + 1
    return n


def _paid_sale_for_order(db: Session, order_id: UUID) -> Sale:
    sale = db.execute(
        select(Sale).where(
            Sale.customer_order_id == order_id, Sale.status == "paid"
        )
    ).scalar_one_or_none()
    if sale is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "La orden no tiene una venta pagada asociada"
        )
    return sale


def generate_for_order(db: Session, order_id: UUID, user: User, prefix: str = "") -> Invoice:
    """Idempotente: si la venta de la order ya tiene factura, la devuelve."""
    order = get_or_404(db, CustomerOrder, order_id, "Order not found")
    if order.status != "pagada":
        raise HTTPException(status.HTTP_409_CONFLICT, "La orden no está pagada")

    sale = _paid_sale_for_order(db, order.id)

    existing = db.execute(
        select(Invoice).where(Invoice.sale_id == sale.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    try:
        number = _next_number(db, prefix)
        invoice = Invoice(
            sale_id=sale.id,
            customer_order_id=order.id,
            prefix=prefix,
            number=number,
            customer_name=sale.customer_name,
            subtotal=sale.subtotal,
            discount=sale.discount,
            tax=sale.tax,
            tip=sale.tip,
            total=sale.total,
            status="issued",
            user_id=user.id,
            user_name=user.name,
        )
        db.add(invoice)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error generando la factura")
        raise

    db.refresh(invoice)
    return invoice


def generate_all_for_table(db: Session, table_id: UUID, user: User, prefix: str = "") -> list[Invoice]:
    """Cierre de mesa: una factura por cada order pagada de la mesa (salta las ya
    facturadas)."""
    table = get_or_404(db, DiningTable, table_id, "Table not found")
    orders = db.execute(
        select(CustomerOrder).where(
            CustomerOrder.dining_table_id == table.id,
            CustomerOrder.status == "pagada",
        ).order_by(CustomerOrder.created_at)
    ).scalars().all()

    invoices: list[Invoice] = []
    for order in orders:
        # generate_for_order es idempotente y dueño de su transacción.
        invoices.append(generate_for_order(db, order.id, user, prefix=prefix))
    return invoices


def serialize_invoice(db: Session, invoice: Invoice) -> InvoiceResponse:
    items = db.execute(
        select(SaleItem).where(SaleItem.sale_id == invoice.sale_id)
    ).scalars().all()
    resp = InvoiceResponse.model_validate(invoice)
    resp.items = [InvoiceItemResponse.model_validate(it) for it in items]
    return resp


def get_invoice(db: Session, invoice_id: UUID) -> InvoiceResponse:
    invoice = get_or_404(db, Invoice, invoice_id, "Invoice not found")
    return serialize_invoice(db, invoice)


def list_invoices(
    db: Session, *, table_id: UUID | None = None, order_id: UUID | None = None
) -> list[InvoiceResponse]:
    stmt = select(Invoice)
    if order_id is not None:
        stmt = stmt.where(Invoice.customer_order_id == order_id)
    if table_id is not None:
        stmt = stmt.join(Sale, Sale.id == Invoice.sale_id).where(
            Sale.dining_table_id == table_id
        )
    stmt = stmt.order_by(Invoice.number)
    invoices = db.execute(stmt).scalars().all()
    return [serialize_invoice(db, inv) for inv in invoices]
