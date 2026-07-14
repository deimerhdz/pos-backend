"""Checkout de venta: arma la venta con snapshots inmutables, valida el pago,
la liga al turno de caja y descuenta inventario (receta + opciones). Dueño de la
transacción."""
import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.core.models import User
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.option import Option
from app.models.cash_shift import CashShift
from app.models.payment import Payment, PaymentMethod
from app.models.sale import Sale, SaleItem
from app.api.v1.sales.consumption import deduct_sale
from app.api.v1.sales.schemas import SaleCreate

logger = logging.getLogger(__name__)


def checkout(db: Session, data: SaleCreate, cashier: User) -> Sale:
    shift = get_or_404(db, CashShift, data.cash_shift_id, "Shift not found")
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "El turno de caja está cerrado")

    try:
        sale = Sale(
            cash_shift_id=shift.id,
            dining_session_id=data.dining_session_id,
            dining_table_id=data.dining_table_id,
            user_id=cashier.id,
            user_name=cashier.name,
            customer_name=data.customer_name,
            discount=data.discount,
            tax=data.tax,
            tip=data.tip,
            status="issued",
        )
        db.add(sale)
        db.flush()

        subtotal = Decimal("0")
        for line in data.items:
            variant = get_or_404(db, ProductVariant, line.product_variant_id, "Variant not found")
            product = db.get(Product, variant.product_id)
            description = f"{product.name} - {variant.name}" if product else variant.name

            unit_price = Decimal(variant.price)
            options_snapshot: list[dict] = []
            for opt_id in line.option_ids:
                option = get_or_404(db, Option, opt_id, f"Option {opt_id} not found")
                unit_price += Decimal(option.extra_price)
                options_snapshot.append({
                    "option_id": str(option.id),
                    "name": option.name,
                    "extra_price": str(option.extra_price),
                })

            line_total = unit_price * Decimal(line.quantity)
            subtotal += line_total
            db.add(SaleItem(
                sale_id=sale.id,
                product_variant_id=variant.id,
                description=description,
                options=options_snapshot,
                quantity=line.quantity,
                unit_price=unit_price,
                line_total=line_total,
            ))

        total = subtotal - Decimal(data.discount) + Decimal(data.tax) + Decimal(data.tip)
        if total < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El total no puede ser negativo")

        paid = Decimal("0")
        for p in data.payments:
            get_or_404(db, PaymentMethod, p.payment_method_id, "Payment method not found")
            db.add(Payment(
                sale_id=sale.id, payment_method_id=p.payment_method_id,
                amount=p.amount, reference=p.reference,
            ))
            paid += Decimal(p.amount)

        if paid < total:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El pago ({paid}) no cubre el total ({total})",
            )

        sale.subtotal = subtotal
        sale.total = total
        sale.status = "paid"

        # La sesión tiene autoflush=False; forzamos el flush para que deduct_sale
        # vea los sale_items recién insertados.
        db.flush()

        # Descontar inventario (receta + opciones). Puede lanzar InsufficientStockError.
        deduct_sale(db, sale, user_id=cashier.id)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Error en checkout de venta")
        raise

    return db.execute(select(Sale).where(Sale.id == sale.id)).scalar_one()
