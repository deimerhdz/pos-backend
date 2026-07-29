"""Checkout de venta: arma la venta con snapshots inmutables, valida el pago,
la liga al turno de caja y descuenta inventario (receta + opciones). Dueño de la
transacción."""
import logging
from datetime import datetime, timezone
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
from app.api.v1.sales.builder import SaleLine, build_sale, ensure_open_shift
from app.api.v1.sales.schemas import SaleCreate
from app.api.v1.promotions import service as promotions

logger = logging.getLogger(__name__)


def checkout(db: Session, data: SaleCreate, cashier: User, *, invoice_prefix: str = "") -> Sale:
    """Venta de mostrador: arma la venta, descuenta inventario y emite factura.

    Usa `build_sale` —el mismo constructor que el cobro de mesa— para no volver a
    tener dos implementaciones divergentes de lo mismo. Lo propio de este camino y
    que no vive en el builder: las **promociones** (que ajustan el descuento antes
    de construir) y el **descuento de inventario**, porque aquí no hubo un paso de
    confirmación previo que ya lo hiciera.
    """
    shift = ensure_open_shift(db, data.cash_shift_id)

    try:
        # 1. Resolver y valorar las líneas (snapshot de precio: variante + opciones).
        lines: list[SaleLine] = []
        promo_lines: list[dict] = []
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

            lines.append(SaleLine(
                product_variant_id=variant.id,
                description=description,
                options=options_snapshot,
                quantity=line.quantity,
                unit_price=unit_price,
            ))
            promo_lines.append({
                "product_id": variant.product_id,
                "category_id": product.category_id if product else None,
                "quantity": line.quantity,
                "line_total": unit_price * Decimal(line.quantity),
            })

        # 2. Descuento automático por promociones (RF-012), sumado al manual.
        promo_discount, promo_id = promotions.evaluate(
            db, promo_lines, datetime.now(timezone.utc)
        )

        sale = build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=data.payments,
            discount=Decimal(data.discount) + promo_discount,
            tax=data.tax,
            tip=data.tip,
            customer_name=data.customer_name,
            dining_table_id=data.dining_table_id,
            participant_id=data.participant_id,
            promotion_id=promo_id,
            invoice_prefix=invoice_prefix,
        )

        # La sesión tiene autoflush=False; forzamos el flush para que deduct_sale
        # vea los sale_items recién insertados.
        db.flush()

        # 3. Inventario: aquí sí descuenta (mesa ya lo hizo al confirmar).
        #    Puede lanzar InsufficientStockError y tumbar toda la transacción.
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
