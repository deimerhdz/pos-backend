"""Deducción de inventario al cobrar una venta (reemplaza el trigger
`fn_deduct_inventory_on_sale` de schema.sql en la capa de aplicación).

Qué consume cada línea lo decide `catalog/consumption_plan.py`, compartido con el
camino de órdenes; la única diferencia de la venta es que las opciones se leen del
snapshot JSONB `sale_items.options` en vez de la tabla relacional.
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import inventory_reasons as reasons
from app.models.option import Option
from app.models.sale import Sale, SaleItem
from app.api.v1.inventory.stock import lock_items, record_movement
from app.api.v1.catalog.consumption_plan import (
    ensure_lines_consume_inventory,
    plan_line_consumption,
    required_consumption,
)


def _sale_item_options(db: Session, si: SaleItem) -> list[Option]:
    """Opciones vivas de una línea de venta, resueltas desde el snapshot JSONB."""
    options: list[Option] = []
    for opt in (si.options or []):
        option_id = opt.get("option_id")
        if not option_id:
            continue
        option = db.get(Option, UUID(str(option_id)))
        if option is not None:
            options.append(option)
    return options


def deduct_sale(db: Session, sale: Sale, user_id: UUID | None) -> None:
    items = db.execute(
        select(SaleItem).where(SaleItem.sale_id == sale.id)
    ).scalars().all()

    # Las opciones se resuelven una sola vez: `_sale_item_options` hace un `db.get`
    # por opción y aquí se recorre tres veces (guarda, pre-bloqueo y descuento).
    entries = [(si, _sale_item_options(db, si)) for si in items]

    # Este camino no tenía la guarda que sí protege las órdenes, así que una variante
    # sin receta se cobraba por caja sin mover stock. Con slots el agujero crece: una
    # receta solo-slot sin opción elegida tampoco descontaría nada.
    ensure_lines_consume_inventory(
        db, [(si.product_variant_id, si.quantity, options) for si, options in entries]
    )

    # Pre-bloqueo en orden canónico de id: sin esto, una venta de mostrador y una
    # confirmación de mesa que compartan insumos pueden deadlockear entre sí.
    needed: set[UUID] = set()
    for si, options in entries:
        needed |= set(
            required_consumption(db, si.product_variant_id, si.quantity, options)
        )
    lock_items(db, needed)

    for si, options in entries:
        for line in plan_line_consumption(
            db, si.product_variant_id, si.quantity, options
        ):
            record_movement(
                db, line.inventory_item_id, type="out", quantity=line.quantity,
                reason=reasons.VENTA, reference_type=reasons.REF_SALE,
                reference_id=sale.id, user_id=user_id,
            )
