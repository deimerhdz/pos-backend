"""Definición **única** del inventario que consume una línea vendida.

Antes esto vivía triplicado: `deduct_order_item`, `reverse_order_item` (ambos en
orders/consumption.py) y `deduct_sale` (sales/consumption.py) repetían el mismo bucle.
Con los slots de receta la lógica deja de ser trivial, y una reversa que diverja del
descuento descuadra el inventario **para siempre** (nadie concilia movimientos 'in'
contra 'out'). Por eso hay un solo sitio que decide qué se consume, y los tres
llamadores solo eligen la dirección del movimiento.

Una línea consume:

  (a) las líneas fijas de la receta de la variante  → `quantity × cantidad`
  (b) por cada opción elegida con insumo ligado     → `por_opción × cantidad`

donde, para el grupo de esa opción:

    por_opción = quantity_per_option > 0
                 ? quantity_per_option      ← lo define el tamaño, y MANDA
                 : option.item_quantity     ← si el tamaño no lo define, manda la opción

Es **por cada opción elegida**, no el total del grupo: elegir dos sabores de 120 g
descuenta 120 de cada uno.

**Una sola cantidad manda, nunca se suman.** Sumarlas era una trampa: con los sabores en
80 g (su valor histórico) y una ensalada pequeña configurada en 60, cada venta descontaba
140 g y nadie se enteraba hasta el conteo físico. El campo de la opción queda como
respaldo para grupos donde cada opción consume lo suyo (un extra que vale igual en todos
los productos), y deja de aplicar en cuanto un tamaño toma el mando.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.option import Option
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.variant_option_group import VariantOptionGroup

from app.catalog_engine.core import ConsumptionLine


def load_recipe(db: Session, variant_id: UUID) -> list[RecipeItem]:
    """Insumos fijos de la variante."""
    return list(
        db.execute(
            select(RecipeItem).where(RecipeItem.product_variant_id == variant_id)
        ).scalars().all()
    )


def load_variant_groups(db: Session, variant_id: UUID) -> list[VariantOptionGroup]:
    """Grupos que ofrece la variante, con su cardinalidad y su consumo por opción."""
    return list(
        db.execute(
            select(VariantOptionGroup).where(
                VariantOptionGroup.product_variant_id == variant_id
            )
        ).scalars().all()
    )


def group_discounts(db: Session, link: VariantOptionGroup) -> bool:
    """Elegir una opción de este grupo movería stock.

    Dos vías, en el orden de la regla: la cantidad que define el tamaño, o —si no la
    define— la que traiga cada opción por su cuenta."""
    if link.quantity_per_option > 0:
        return True
    return db.execute(
        select(Option.id)
        .where(
            Option.option_group_id == link.option_group_id,
            Option.active.is_(True),
            Option.inventory_item_id.is_not(None),
            Option.item_quantity > 0,
        )
        .limit(1)
    ).first() is not None


def plan_line_consumption(
    db: Session, variant_id: UUID, quantity: int, options: Sequence[Option]
) -> list[ConsumptionLine]:
    """Qué consume una línea de `quantity` unidades de `variant_id` con `options`
    elegidas. Read-only.

    No agrega por insumo: si dos sabores distintos apuntan al mismo insumo se emiten
    dos líneas, y por tanto dos movimientos de kardex. Es deliberado — dos apuntes
    separados son auditables ("2 de fresa, 1 de fresa premium"), uno fusionado no.
    `record_movement` los aplica en secuencia sobre la fila ya bloqueada.
    """
    if not _tracks_inventory(db, variant_id):
        # Producto sin inventario (spec 027): esta es la única función que consumen
        # directamente deduct_order_item/reverse_order_item/deduct_sale para el
        # descuento real — sin este corte, insumos "abandonados" de una
        # configuración anterior seguirían generando movimientos de inventario
        # aunque el switch ya esté apagado.
        return []
    qty = Decimal(quantity)

    lines: list[ConsumptionLine] = [
        ConsumptionLine(ri.inventory_item_id, Decimal(ri.quantity) * qty, "receta")
        for ri in load_recipe(db, variant_id)
    ]

    por_grupo: dict[UUID, Decimal] = {
        g.option_group_id: Decimal(g.quantity_per_option)
        for g in load_variant_groups(db, variant_id)
    }

    for option in options:
        if option.inventory_item_id is None:
            continue
        # None → esta variante no ofrece el grupo de la opción (la validación de
        # selección lo rechaza aparte; aquí solo significa que no aporta cantidad).
        del_grupo = por_grupo.get(option.option_group_id) or Decimal(0)
        # Reemplaza, no suma: ver la cabecera del módulo.
        manda_el_tamano = del_grupo > 0
        per_unit = del_grupo if manda_el_tamano else Decimal(option.item_quantity)
        if per_unit <= 0:
            continue
        lines.append(
            ConsumptionLine(
                option.inventory_item_id,
                per_unit * qty,
                "variante" if manda_el_tamano else "opcion",
            )
        )

    return lines


def required_consumption(
    db: Session, variant_id: UUID, quantity: int, options: Sequence[Option]
) -> dict[UUID, Decimal]:
    """El plan agregado por insumo. Es lo que necesitan el chequeo preventivo de
    disponibilidad y el pre-bloqueo de filas, a los que solo les importa el total
    por insumo, no de dónde sale."""
    req: dict[UUID, Decimal] = defaultdict(Decimal)
    for line in plan_line_consumption(db, variant_id, quantity, options):
        req[line.inventory_item_id] += line.quantity
    return dict(req)


def variant_label(db: Session, variant_id: UUID) -> str:
    """'Producto · Variante' para poder nombrar el problema en el mensaje."""
    row = db.execute(
        select(Product.name, ProductVariant.name)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id == variant_id)
    ).first()
    return f"{row[0]} · {row[1]}" if row else str(variant_id)


def _tracks_inventory(db: Session, variant_id: UUID) -> bool:
    """Si el producto de esta variante maneja inventario (spec 027). `True` si la
    variante no se encuentra — nunca exime en silencio una variante inexistente."""
    value = db.execute(
        select(Product.tracks_inventory)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .where(ProductVariant.id == variant_id)
    ).scalar_one_or_none()
    return True if value is None else value


def ensure_lines_consume_inventory(
    db: Session, entries: Sequence[tuple[UUID, int, Sequence[Option]]]
) -> None:
    """Rechaza el lote si alguna línea no descontaría **nada**.

    Una variante que se vende sin mover stock no deja rastro en el kardex y sobrestima
    el inventario en silencio hasta el conteo físico. Es peor que un error, porque
    nadie se entera; mejor parar y configurar la receta.

    Distingue "sin nada configurado" de "configurado, pero el cliente no eligió": si la
    variante sí ofrece un grupo que descuenta, el mensaje de siempre sería falso y
    mandaría al dueño a Productos → Recetas, donde no encontraría nada raro. Una
    variante con insumos fijos **y** un grupo sin elegir pasa: ya descuenta algo, y si
    el grupo es opcional (`min_select = 0`) no elegir sabor es una decisión legítima del
    comensal.

    Vive aquí, y no en cada endpoint, porque es el paso común de todos los caminos que
    descuentan; validarlo en uno solo dejaría los otros abiertos, que es justo como se
    coló el problema la primera vez.
    """
    sin_receta: list[str] = []
    sin_eleccion: list[str] = []

    for variant_id, quantity, options in entries:
        if not _tracks_inventory(db, variant_id):
            # Producto sin inventario (spec 027): exento de esta validación, sin
            # importar si la presentación tiene o no receta/grupos configurados.
            continue
        if required_consumption(db, variant_id, quantity, options):
            continue
        etiqueta = variant_label(db, variant_id)
        configurada = load_recipe(db, variant_id) or any(
            group_discounts(db, g) for g in load_variant_groups(db, variant_id)
        )
        if configurada:
            sin_eleccion.append(etiqueta)
        else:
            sin_receta.append(etiqueta)

    if sin_receta:
        nombres = ", ".join(f"«{n}»" for n in dict.fromkeys(sin_receta))
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    f"{nombres} no tiene receta configurada, así que venderlo no "
                    "descontaría inventario. Cárgasela en Productos → Recetas para "
                    "poder venderlo."
                ),
                "variantes_sin_receta": list(dict.fromkeys(sin_receta)),
            },
        )

    if sin_eleccion:
        nombres = ", ".join(f"«{n}»" for n in dict.fromkeys(sin_eleccion))
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    f"{nombres} consume inventario según la opción que elija el "
                    "cliente, pero no se eligió ninguna, así que venderlo no "
                    "descontaría nada."
                ),
                "variantes_sin_opcion": list(dict.fromkeys(sin_eleccion)),
            },
        )
