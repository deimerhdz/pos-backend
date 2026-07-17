"""Helpers de línea compartidos entre carrito (Fase 3) y consolidación (Fase 4):
snapshot de precio y chequeo preventivo de disponibilidad contra stock único.

El precio es un snapshot (variante + extras de opción). La disponibilidad NO
reserva ni bloquea (a diferencia de `record_movement` en inventory/stock.py, que
sí bloquea y descuenta en la venta/consolidación); es un chequeo best-effort de
UX que puede quedar obsoleto para cuando se consolide.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.models.option import Option
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.inventory_item import InventoryItem


def load_valid_options(db: Session, option_ids: list[UUID]) -> list[Option]:
    """Carga las opciones (dedup) validando existencia y que estén activas."""
    seen: set[UUID] = set()
    options: list[Option] = []
    for opt_id in option_ids:
        if opt_id in seen:
            continue
        seen.add(opt_id)
        option = get_or_404(db, Option, opt_id, f"Option {opt_id} not found")
        if not option.active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Opción inactiva: {opt_id}"
            )
        options.append(option)
    return options


def compute_line_price(variant: ProductVariant, options: list[Option]) -> Decimal:
    """Snapshot de precio de una línea: precio de la variante + extras de opción."""
    price = Decimal(variant.price)
    for option in options:
        price += Decimal(option.extra_price)
    return price


def required_consumption(
    db: Session, variant_id: UUID, quantity: int, options: list[Option]
) -> dict[UUID, Decimal]:
    """Consumo de inventario que implicaría una línea (receta×cantidad + insumos
    de opciones×cantidad), agregado por `inventory_item_id`. Read-only."""
    req: dict[UUID, Decimal] = defaultdict(Decimal)
    qty = Decimal(quantity)

    recipe = db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == variant_id)
    ).scalars().all()
    for ri in recipe:
        req[ri.inventory_item_id] += Decimal(ri.quantity) * qty

    for option in options:
        if option.inventory_item_id is not None and Decimal(option.item_quantity) > 0:
            req[option.inventory_item_id] += Decimal(option.item_quantity) * qty

    return req


def check_availability(
    db: Session, required: dict[UUID, Decimal], *, extra_context: str = ""
) -> None:
    """Chequeo preventivo (sin lock ni reserva): rechaza con 409 si algún insumo
    no tiene `current_stock` suficiente para el consumo `required` agregado."""
    for item_id, need in required.items():
        if need <= 0:
            continue
        item = db.get(InventoryItem, item_id)
        if item is None:
            continue
        if Decimal(item.current_stock) < need:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "error": "Stock insuficiente",
                    "insumo": item.name,
                    "disponible": str(item.current_stock),
                    "requerido": str(need),
                    "contexto": extra_context,
                },
            )
