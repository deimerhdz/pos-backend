"""Checkout de venta: arma la venta con snapshots inmutables, valida el pago,
la liga al turno de caja y descuenta inventario (receta + opciones). Dueño de la
transacción."""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import cast, func, select, String
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.core.crud import get_or_404
from app.core.models import User
from app.models.invoice import Invoice
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.option import Option
from app.models.cash_shift import CashShift
from app.models.payment import Payment, PaymentMethod
from app.models.sale import Sale, SaleItem
from app.api.v1.catalog.line_pricing import compute_line_price, load_valid_options
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
        now = datetime.now(timezone.utc)

        # 1. Resolver y valorar las líneas (snapshot de precio: variante + opciones).
        #    Un ítem con combo_id se expande en sus componentes reales a precio
        #    normal; su ahorro se calcula aparte y no entra a promo_lines (un
        #    combo y un percent/fixed nunca se acumulan sobre la misma línea).
        lines: list[SaleLine] = []
        promo_lines: list[dict] = []
        combo_ids_used: set[UUID] = set()
        for line in data.items:
            if line.combo_id is not None:
                combo_ids_used.add(line.combo_id)
                for component in promotions.expand_combo(db, line.combo_id, line.quantity, now):
                    lines.append(SaleLine(
                        product_variant_id=component.product_variant_id,
                        description=component.description,
                        options=[],
                        quantity=component.quantity,
                        unit_price=component.unit_price,
                        combo_id=line.combo_id,
                    ))
                continue

            variant = get_or_404(db, ProductVariant, line.product_variant_id, "Variant not found")
            product = db.get(Product, variant.product_id)
            description = f"{product.name} - {variant.name}" if product else variant.name

            # Deduplica, exige que estén activas y valida la selección contra los
            # grupos del producto. Antes este bucle cargaba las opciones a mano y se
            # saltaba las tres cosas.
            options = load_valid_options(db, line.option_ids, variant=variant)
            unit_price = compute_line_price(variant, options)
            options_snapshot: list[dict] = [
                {
                    "option_id": str(option.id),
                    "name": option.name,
                    "extra_price": str(option.extra_price),
                }
                for option in options
            ]

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

        # 2. Descuento automático: promociones percent/fixed (RF-012) sobre las
        #    líneas normales, más el ahorro de los combos seleccionados. Ambos se
        #    suman al descuento manual que haya escrito el cajero.
        promo_discount, promo_id = promotions.evaluate(db, promo_lines, now)
        combo_discount = promotions.combo_discount_for_lines(db, lines, now)
        final_promotion_id = next(iter(combo_ids_used)) if len(combo_ids_used) == 1 else promo_id

        sale = build_sale(
            db,
            lines=lines,
            shift=shift,
            cashier=cashier,
            payments=data.payments,
            discount=Decimal(data.discount) + promo_discount + combo_discount,
            tax=data.tax,
            tip=data.tip,
            customer_name=data.customer_name,
            dining_table_id=data.dining_table_id,
            participant_id=data.participant_id,
            promotion_id=final_promotion_id,
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


def list_sales_query(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    invoice_reference: str | None = None,
) -> Select:
    """Construye el `Select` de ventas para el listado paginado (GET /sales)."""
    stmt = (
        select(Sale)
        .options(
            selectinload(Sale.items),
            selectinload(Sale.payments),
            selectinload(Sale.invoice),
            selectinload(Sale.dining_table),
        )
        .order_by(Sale.sold_at.desc())
    )
    if status:
        stmt = stmt.where(Sale.status == status)
    if date_from:
        stmt = stmt.where(Sale.sold_at >= date_from)
    if date_to:
        stmt = stmt.where(Sale.sold_at < date_to + timedelta(days=1))
    if invoice_reference:
        # No hay columna "referencia": se reconstruye prefix + número (6 dígitos)
        # tal como se imprime en el ticket (ver Invoice.full_number).
        full_number = func.concat(Invoice.prefix, func.lpad(cast(Invoice.number, String), 6, "0"))
        stmt = stmt.join(Invoice, Invoice.sale_id == Sale.id).where(
            full_number.ilike(f"%{invoice_reference.strip()}%")
        )
    return stmt
