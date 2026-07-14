"""Deducción de inventario al cobrar una venta (reemplaza el trigger
`fn_deduct_inventory_on_sale` de schema.sql en la capa de aplicación).

Para cada línea vendida descuenta (a) los insumos de la receta de la variante y
(b) los insumos de las opciones elegidas. Escribe movimientos 'out' en el kardex.
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.recipe_item import RecipeItem
from app.models.option import Option
from app.models.sale import Sale, SaleItem
from app.api.v1.inventory.stock import record_movement


def deduct_sale(db: Session, sale: Sale, user_id: UUID | None) -> None:
    items = db.execute(
        select(SaleItem).where(SaleItem.sale_id == sale.id)
    ).scalars().all()

    for si in items:
        qty = Decimal(si.quantity)

        # (a) receta de la variante
        recipe = db.execute(
            select(RecipeItem).where(RecipeItem.product_variant_id == si.product_variant_id)
        ).scalars().all()
        for ri in recipe:
            record_movement(
                db, ri.inventory_item_id, type="out", quantity=ri.quantity * qty,
                reason="Consumo de receta en venta", reference_type="sale",
                reference_id=sale.id, user_id=user_id,
            )

        # (b) opciones elegidas (snapshot en si.options: [{option_id, ...}])
        for opt in (si.options or []):
            option_id = opt.get("option_id")
            if not option_id:
                continue
            option = db.get(Option, UUID(str(option_id)))
            if option is None or option.inventory_item_id is None or option.item_quantity <= 0:
                continue
            record_movement(
                db, option.inventory_item_id, type="out",
                quantity=option.item_quantity * qty,
                reason="Consumo de opción en venta", reference_type="sale",
                reference_id=sale.id, user_id=user_id,
            )
