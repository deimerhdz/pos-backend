"""Facturación: una factura interna (snapshot inmutable) por **venta**.

La unidad es la venta, no el pedido: `Invoice.sale_id` es único, y tras el cobro
por sesión un cierre `split` emite N ventas mientras que una venta de mostrador no
cuelga de ningún pedido. Facturar por pedido dejaba fuera esos casos.

La emisión ocurre dentro de la transacción del cobro (`build_sale`), así que
`issue_for_sale` **no hace commit**: una factura sin su venta, o al revés, sería
peor que no tener factura.

Consecutivo serializado con lock del contador. DIAN fuera de v1."""
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


def issue_for_sale(db: Session, sale: Sale, *, user: User, prefix: str = "") -> Invoice:
    """Emite la factura de una venta. **No hace commit** (se une a la transacción
    del cobro) y es idempotente: si la venta ya tiene factura, devuelve la existente
    en vez de chocar con la constraint única de `sale_id`.

    El lock del contador serializa las ventas del mismo prefijo mientras dura la
    transacción. Es el precio de un consecutivo sin huecos: si esto hiciera rollback,
    el número se libera y no queda salto.
    """
    existing = db.execute(
        select(Invoice).where(Invoice.sale_id == sale.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    invoice = Invoice(
        sale_id=sale.id,
        customer_order_id=sale.customer_order_id,
        prefix=prefix or "",
        number=_next_number(db, prefix or ""),
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
    return invoice


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
