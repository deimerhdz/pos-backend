"""Cálculo de impuestos de una línea de orden desde los tax_links (Fase 1).

Aplica los impuestos asociados a la variante y/o al producto de la línea sobre el
`base` (subtotal de la línea). Devuelve (tax_amount, added):
- tax_amount: impuesto total de la línea (para reporte), sea inclusivo o exclusivo.
- added: monto que se SUMA al total (solo impuestos exclusivos).
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.tax import Tax
from app.models.tax_link import TaxLink


def compute_line_tax(
    db: Session, *, product_id: UUID | None, variant_id: UUID | None, base: Decimal
) -> tuple[Decimal, Decimal]:
    conds = []
    if variant_id is not None:
        conds.append(TaxLink.variant_id == variant_id)
    if product_id is not None:
        conds.append(TaxLink.product_id == product_id)
    if not conds:
        return Decimal("0"), Decimal("0")

    taxes = db.execute(
        select(Tax)
        .join(TaxLink, TaxLink.tax_id == Tax.id)
        .where(or_(*conds), Tax.active == True)
    ).scalars().all()

    tax_amount = Decimal("0")
    added = Decimal("0")
    base = Decimal(base)
    for tax in taxes:
        rate = Decimal(tax.rate)
        if tax.inclusive:
            # el impuesto ya está dentro del precio: porción incluida
            portion = base * rate / (Decimal("100") + rate)
            tax_amount += portion
        else:
            t = base * rate / Decimal("100")
            tax_amount += t
            added += t

    return tax_amount, added
