from app.core.models import Base, UUIDPrimaryKeyMixin
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import (
    String, Integer, Numeric, ForeignKey, DateTime, func, CheckConstraint, UniqueConstraint,
)
from sqlalchemy.orm import mapped_column, Mapped
from typing import Optional
from decimal import Decimal
from datetime import datetime


class Invoice(UUIDPrimaryKeyMixin, Base):
    """Factura interna (snapshot inmutable de una venta). Consecutivo por
    `(prefix, number)`. Una por venta (`sale_id` unique) → idempotencia.

    DIAN-ready: `cufe`/`dian_status`/`dian_sent_at` quedan para la integración
    de facturación electrónica (no usados en v1)."""

    __tablename__ = "invoices"

    sale_id: Mapped[UUID] = mapped_column(
        ForeignKey("sales.id"), nullable=False, unique=True, index=True
    )

    customer_order_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("customer_orders.id"), nullable=True, index=True
    )

    prefix: Mapped[str] = mapped_column(String(20), nullable=False, server_default="")
    number: Mapped[int] = mapped_column(Integer, nullable=False)

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    tip: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="issued")

    issued_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Emisor: referencia blanda a shared.users.id + snapshot.
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # DIAN-ready (nullable, sin uso en v1).
    cufe: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dian_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dian_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('issued', 'void')", name="ck_invoice_status"),
        UniqueConstraint("prefix", "number", name="uq__invoices__prefix__number"),
        {"schema": "tenant"},
    )


class InvoiceCounter(UUIDPrimaryKeyMixin, Base):
    """Consecutivo de facturación por prefijo. Se bloquea `FOR UPDATE` para
    asignar el siguiente número sin condiciones de carrera. DIAN-ready: un
    prefijo mapea a una resolución/rango autorizado."""

    __tablename__ = "invoice_counters"

    prefix: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, server_default="")

    next_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = ({"schema": "tenant"},)
