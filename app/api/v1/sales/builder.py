"""Construcción de una venta, compartida por los dos caminos de cobro.

Antes había dos implementaciones paralelas y divergentes de lo mismo — una en
`sales/service.py:checkout` (mostrador) y otra en `orders/checkout.py:pay_order`
(mesa) — con ~80 líneas duplicadas. Divergían en detalles que costaban dinero: el
cobro de mesa no seteaba `paid_amount`/`change_given`, así que el cambio entregado
no se descontaba del efectivo esperado y el arqueo del turno quedaba descuadrado.

Lo que **sigue siendo distinto por diseño** es el descuento de inventario, y por eso
no vive aquí:

- mostrador: descuenta al vender (`deduct_sale`), porque no hubo paso previo;
- mesa: ya descontó al confirmar el pedido, así que cobrar no vuelve a descontar.

Quien llama decide; `build_sale` nunca toca stock.
"""
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.core.models import User
from app.models.cash_shift import CashShift
from app.models.payment import Payment, PaymentMethod
from app.models.sale import Sale, SaleItem


class SaleLine:
    """Línea ya resuelta y valorada, lista para convertirse en `SaleItem`.

    El precio llega calculado por el caller (snapshot de la variante + extras de
    opción, o el precio congelado del `order_item`), porque cada camino lo obtiene
    de un sitio distinto."""

    __slots__ = ("product_variant_id", "description", "options", "quantity",
                 "unit_price", "line_total")

    def __init__(
        self,
        *,
        product_variant_id: UUID,
        description: str,
        options: list[dict],
        quantity: int,
        unit_price: Decimal,
    ) -> None:
        self.product_variant_id = product_variant_id
        self.description = description
        self.options = options
        self.quantity = quantity
        self.unit_price = Decimal(unit_price)
        self.line_total = self.unit_price * Decimal(quantity)


def ensure_open_shift(db: Session, cash_shift_id: UUID) -> CashShift:
    shift = get_or_404(db, CashShift, cash_shift_id, "Shift not found")
    if shift.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "El turno de caja está cerrado")
    return shift


def build_sale(
    db: Session,
    *,
    lines: list[SaleLine],
    shift: CashShift,
    cashier: User,
    payments: list,
    discount: Decimal = Decimal("0"),
    tax: Decimal = Decimal("0"),
    tip: Decimal = Decimal("0"),
    customer_name: str | None = None,
    dining_table_id: UUID | None = None,
    table_session_id: UUID | None = None,
    participant_id: UUID | None = None,
    customer_order_id: UUID | None = None,
    promotion_id: UUID | None = None,
    invoice_prefix: str = "",
) -> Sale:
    """Arma `Sale` + `SaleItem` + `Payment`, deja la venta en `paid` y **emite su
    factura**.

    Valida que el total no sea negativo y que el pago lo cubra. **No hace commit
    ni toca inventario**: se une a la transacción del caller.

    La factura se emite aquí y no en cada camino de cobro por la misma razón que
    todo lo demás vive aquí: hay cuatro formas de cobrar (mostrador, cierre
    unificado, cierre dividido y el `pay_order` legacy) y hacerlo en cada una
    garantizaba que alguna se quedara fuera —que es exactamente lo que pasaba
    cuando facturar dependía de que alguien pulsara un botón.
    """
    if not lines:
        raise HTTPException(status.HTTP_409_CONFLICT, "La venta no tiene ítems cobrables")

    sale = Sale(
        cash_shift_id=shift.id,
        dining_table_id=dining_table_id,
        table_session_id=table_session_id,
        participant_id=participant_id,
        customer_order_id=customer_order_id,
        user_id=cashier.id,
        user_name=cashier.name,
        customer_name=customer_name,
        discount=discount,
        tax=tax,
        tip=tip,
        promotion_id=promotion_id,
        status="issued",
    )
    db.add(sale)
    db.flush()

    subtotal = Decimal("0")
    for line in lines:
        subtotal += line.line_total
        db.add(SaleItem(
            sale_id=sale.id,
            product_variant_id=line.product_variant_id,
            description=line.description,
            options=line.options,
            quantity=line.quantity,
            unit_price=line.unit_price,
            line_total=line.line_total,
        ))

    total = subtotal - Decimal(discount) + Decimal(tax) + Decimal(tip)
    if total < 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "El total no puede ser negativo"
        )

    paid = Decimal("0")
    for p in payments:
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
    # RF-029: `reconcile` calcula el efectivo esperado como
    # Σ pagos en efectivo − Σ change_given. Sin estos dos campos el cambio
    # entregado nunca se descuenta y el arqueo del turno queda descuadrado.
    sale.paid_amount = paid
    sale.change_given = paid - total
    sale.status = "paid"

    # Factura: en la misma transacción, con los totales ya cerrados. Import local
    # para no crear un ciclo (invoices importa modelos de ventas).
    from app.api.v1.invoices.service import issue_for_sale

    issue_for_sale(db, sale, user=cashier, prefix=invoice_prefix)
    return sale
