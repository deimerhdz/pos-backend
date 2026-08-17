"""Helpers de línea compartidos entre carrito (Fase 3) y consolidación (Fase 4):
validación de la selección de opciones y chequeo preventivo de disponibilidad
contra stock único.

La disponibilidad NO reserva ni bloquea (a diferencia de `record_movement` en
inventory/stock.py, que sí bloquea y descuenta en la venta/consolidación); es
un chequeo best-effort de UX que puede quedar obsoleto para cuando se
consolide.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crud import get_or_404
from app.models.inventory_item import InventoryItem
from app.models.option import Option
from app.models.option_group import OptionGroup
from app.models.product_variant import ProductVariant
from app.models.variant_option_group import VariantOptionGroup

from app.catalog_engine.core import _exige_maximo
from app.catalog_engine.consumption import load_variant_groups

logger = logging.getLogger(__name__)


def load_valid_options(
    db: Session, option_ids: list[UUID], *, variant: ProductVariant | None = None
) -> list[Option]:
    """Carga las opciones (dedup) validando existencia y que estén activas.

    Pasar `variant` valida además la selección contra los grupos del producto (ver
    `validate_option_selection`). Es opcional solo por compatibilidad con los pocos
    llamadores que aún no tienen la variante a mano; **pasarla siempre que se pueda**.
    """
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
    if variant is not None:
        validate_option_selection(db, variant, options)
    return options


def grupos_que_descuentan(db: Session, links: Sequence[VariantOptionGroup]) -> set[UUID]:
    """De estos grupos, cuáles mueven inventario al elegir una opción.

    Hay **dos** vías y contar solo una deja fuera la mitad del catálogo:

    - `variant_option_groups.quantity_per_option`: el tamaño reparte una cantidad
      por cada opción elegida (la copa grande, 120 g de cada sabor);
    - `options.item_quantity`: la opción descuenta lo suyo en todo el catálogo.

    Basta con que la opción descuente por su cuenta para que el grupo importe:
    elegir menos opciones descuenta menos, con independencia de dónde esté
    escrita la cantidad.
    """
    gids = {l.option_group_id for l in links}
    if not gids:
        return set()
    por_grupo = {l.option_group_id for l in links if l.quantity_per_option > 0}
    por_opcion = set(db.execute(
        select(Option.option_group_id)
        .where(Option.option_group_id.in_(gids), Option.item_quantity > 0)
        .distinct()
    ).scalars().all())
    return por_grupo | por_opcion


def validate_option_selection(
    db: Session, variant: ProductVariant, options: Sequence[Option]
) -> None:
    """Comprueba que la selección respeta los grupos que ofrece **esta variante** y sus
    `min_select`/`max_select`.

    Hasta hace poco esto no se validaba en ningún camino de pedido (solo el frontend), y
    era un fallo de UX tolerable. **Con consumo por opción deja de serlo**: lo que se
    descuenta es `nº de opciones elegidas × quantity_per_option`, así que aceptar cinco
    opciones en un grupo `max_select=1` descuenta cinco veces el helado.

    Por eso el rodaje es asimétrico: `STRICT_OPTION_SELECTION` gobierna el resto del
    catálogo, pero **los grupos que descuentan se validan siempre**. Ahí no es
    cosmético, es inventario.

    Y por lo mismo un grupo que descuenta y es obligatorio exige el **máximo**, no el
    mínimo (ver `_exige_maximo`): el error simétrico al de arriba es elegir un sabor
    para un helado de tres bolas, que sirve tres y descuenta una.

    Todo sale de la misma tabla y del mismo nivel (la variante). Antes los bounds venían
    del producto y el consumo de la variante, así que un tamaño podía exigir un número
    de sabores pensado para otro.
    """
    links = load_variant_groups(db, variant.id)
    bounds = {l.option_group_id: (l.min_select, l.max_select) for l in links}
    consumen = grupos_que_descuentan(db, links)

    chosen: dict[UUID, int] = defaultdict(int)
    for option in options:
        chosen[option.option_group_id] += 1

    problems: list[tuple[UUID | None, str]] = []

    for gid, count in chosen.items():
        if gid not in bounds:
            problems.append((gid, "no está disponible para esta presentación"))
            continue
        lo, hi = bounds[gid]
        if _exige_maximo(gid, lo, consumen):
            if count != hi:
                problems.append(
                    (gid, f"exige exactamente {hi} opción(es), se enviaron {count}")
                )
        elif count > hi:
            problems.append((gid, f"admite como máximo {hi} opción(es), se enviaron {count}"))
        elif count < lo:
            problems.append((gid, f"exige al menos {lo} opción(es), se enviaron {count}"))

    for gid, (lo, hi) in bounds.items():
        if lo > 0 and gid not in chosen:
            faltan = hi if _exige_maximo(gid, lo, consumen) else lo
            problems.append((gid, f"es obligatorio: elige {faltan} opción(es)"))

    if not problems:
        return

    # Un problema en un grupo que descuenta inventario es siempre bloqueante.
    blocking = [p for p in problems if p[0] in consumen]
    if not blocking and not settings.STRICT_OPTION_SELECTION:
        logger.warning(
            "Selección de opciones inválida (variante %s), tolerada por "
            "STRICT_OPTION_SELECTION=False: %s",
            variant.id, "; ".join(msg for _gid, msg in problems),
        )
        return

    relevant = blocking or problems
    names = {
        gid: name for gid, name in db.execute(
            select(OptionGroup.id, OptionGroup.name).where(
                OptionGroup.id.in_([gid for gid, _ in relevant if gid is not None])
            )
        ).all()
    }
    detalle = "; ".join(
        f"«{names.get(gid, 'grupo')}» {msg}" for gid, msg in relevant
    )
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": f"Selección de opciones inválida: {detalle}", "grupos": detalle},
    )


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
