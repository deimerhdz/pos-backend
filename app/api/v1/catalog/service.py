"""Servicio de catálogo (heladería): variantes vendibles y su receta (BOM).

El precio vive en la variante; la receta liga la variante a insumos de inventario.
Los grupos de opciones (sabores) se gestionan aparte y se asignan al producto.
"""
import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.inventory_item import InventoryItem
from app.models.option_group import OptionGroup
from app.models.variant_option_group import VariantOptionGroup
from app.api.v1.catalog.schemas import VariantSaveIn, RecipeItemIn, VariantOptionGroupIn


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "", (text or "").upper())
    return cleaned[:4] or "X"


def _unique_sku(db: Session, base: str) -> str:
    sku = base
    i = 2
    while db.execute(select(ProductVariant.id).where(ProductVariant.sku == sku)).first() is not None:
        sku = f"{base}-{i}"
        i += 1
    return sku


def _next_display_order(db: Session, product_id: UUID) -> int:
    """Siguiente posición al final para una presentación nueva (spec 042, FR-005).

    Cuenta activas e inactivas del mismo producto -- una presentación desactivada
    sigue ocupando su posición en la secuencia (research.md Decisión 4), así que el
    siguiente hueco libre es siempre el máximo actual + 1, nunca el conteo de filas.
    """
    current_max = db.execute(
        select(func.max(ProductVariant.display_order)).where(
            ProductVariant.product_id == product_id
        )
    ).scalar()
    return (current_max or 0) + 1


def variante_duplicada(
    db: Session,
    product_id: UUID,
    name: str,
    *,
    exclude_id: UUID | None = None,
) -> ProductVariant | None:
    """Variante del mismo producto que ya ocupa ese nombre, esté activa o no.

    Existe porque `DELETE /variants/{id}` es un soft-delete: la fila desactivada sigue
    ocupando el nombre en `uq__product_variants__product_id__name`, así que recrear una
    presentación borrada choca con la constraint. Sin esta comprobación el conflicto
    llegaba al `commit()` y salía como 500 en vez de un 409 accionable.

    La búsqueda es case-insensitive aunque la constraint no lo sea: en una carta
    «Pequeña» y «pequeña» son la misma presentación y dos filas confundirían al cajero.
    Ordena activas primero para que el 409 hable de la que el usuario tiene a la vista.
    """
    stmt = (
        select(ProductVariant)
        .where(
            ProductVariant.product_id == product_id,
            func.lower(func.trim(ProductVariant.name)) == name.strip().lower(),
        )
        .order_by(ProductVariant.active.desc())
    )
    if exclude_id is not None:
        stmt = stmt.where(ProductVariant.id != exclude_id)
    # `.first()` y no `scalar_one_or_none()`: si los datos ya traen dos filas que solo
    # difieren en mayúsculas, esto no debe reventar con MultipleResultsFound.
    return db.execute(stmt).scalars().first()


def ensure_default_variant(db: Session, product: Product, *, price=0) -> ProductVariant:
    """Garantiza que un producto tenga al menos una variante vendible. Los
    productos sin tamaños obtienen una variante 'Single'."""
    existing = db.execute(
        select(ProductVariant).where(ProductVariant.product_id == product.id).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    variant = ProductVariant(
        product_id=product.id,
        name="Single",
        sku=_unique_sku(db, f"{_slug(product.name)}-DEF"),
        price=price,
        active=True,
        display_order=_next_display_order(db, product.id),
    )
    db.add(variant)
    db.flush()
    return variant


# ===================== Guardado consolidado de producto (spec 043) =====================
#
# `reorder_variants`/`VariantReorderError` (el endpoint dedicado de reordenamiento, spec 042)
# se retiraron aquí (A-55, registro-de-anomalias.md): `_assign_display_orders` abajo reemplaza
# su función, dentro del guardado consolidado, con el mismo patrón de dos pasadas.
# `_save_variant_entry`/`_replace_recipe`/`_replace_option_groups` reproducen, a propósito, la
# misma lógica de validación que ya usan `create_variant`/`update_variant`/`set_recipe`/
# `set_variant_option_groups` en `catalog/router.py` -- no se importa de ahí (la capa de servicio
# no depende de la de router) y esas funciones del router siguen intactas mientras existan sus
# endpoints (retiro condicionado a FR-007). La única diferencia real es que estas versiones no
# hacen `commit()` por su cuenta (el llamador -- `ProductService.create_product`/`update_product`
# -- controla una única transacción para todo el árbol, FR-004) y que cada error trae
# `variant_index` para identificar qué entrada del payload falló (research.md Decisión 5).


def _raise_name_conflict(dup: ProductVariant, index: int) -> None:
    if dup.active:
        mensaje = f"Ya existe una variante «{dup.name}» en este producto"
    else:
        mensaje = (
            f"Ya existe una variante «{dup.name}» desactivada en este producto. "
            "Reactívala en vez de crear otra."
        )
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": mensaje,
            "variant_index": index,
            "variant_id": str(dup.id),
            "active": dup.active,
        },
    )


def _ensure_sku_unique(
    db: Session, sku: str, index: int, *, exclude_id: UUID | None = None
) -> None:
    stmt = select(ProductVariant.id).where(ProductVariant.sku == sku)
    if exclude_id is not None:
        stmt = stmt.where(ProductVariant.id != exclude_id)
    if db.execute(stmt).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error": "SKU already exists", "variant_index": index},
        )


def _replace_recipe(
    db: Session, variant_id: UUID, items: list[RecipeItemIn], index: int
) -> None:
    """Reemplazo total de la receta de una presentación -- mismo patrón que `set_recipe`
    (`catalog/router.py`), con `variant_index` en cualquier error."""
    db.execute(RecipeItem.__table__.delete().where(RecipeItem.product_variant_id == variant_id))
    seen: set[UUID] = set()
    for it in items:
        if it.inventory_item_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "Insumo repetido en la receta", "variant_index": index},
            )
        if db.get(InventoryItem, it.inventory_item_id) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "Inventory item not found", "variant_index": index},
            )
        seen.add(it.inventory_item_id)
        db.add(RecipeItem(
            product_variant_id=variant_id,
            inventory_item_id=it.inventory_item_id,
            quantity=it.quantity,
        ))


def _replace_option_groups(
    db: Session, variant_id: UUID, groups: list[VariantOptionGroupIn], index: int
) -> None:
    """Reemplazo total de los grupos de opciones de una presentación -- mismo patrón que
    `set_variant_option_groups` (`catalog/router.py`), con `variant_index` en cualquier error."""
    db.execute(
        VariantOptionGroup.__table__.delete().where(
            VariantOptionGroup.product_variant_id == variant_id
        )
    )
    seen: set[UUID] = set()
    for g in groups:
        if g.option_group_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Grupo de opciones repetido en esta presentación",
                    "variant_index": index,
                },
            )
        group = db.get(OptionGroup, g.option_group_id)
        if group is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={"error": "Option group not found", "variant_index": index},
            )
        if not group.active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": f"El grupo «{group.name}» está inactivo", "variant_index": index},
            )
        seen.add(g.option_group_id)
        db.add(VariantOptionGroup(
            product_variant_id=variant_id,
            option_group_id=g.option_group_id,
            min_select=g.min_select,
            max_select=g.max_select,
            quantity_per_option=g.quantity_per_option,
        ))


def _save_variant_entry(
    db: Session,
    product: Product,
    entry: VariantSaveIn,
    index: int,
    existing_by_id: dict[UUID, ProductVariant],
) -> ProductVariant:
    """Crea o actualiza una presentación dentro de un guardado consolidado (spec 043, FR-001/
    FR-002). `existing_by_id` son las presentaciones que ya pertenecen a `product` (para poder
    rechazar un `id` que no le pertenece). `index` es la posición de `entry` en la lista `variants`
    del body -- se agrega a cualquier error para identificar qué presentación falló (FR-004).

    No asigna el `display_order` final (lo hace `_assign_display_orders` una vez resueltas todas
    las entradas) ni hace `commit()` -- el llamador controla la transacción completa.
    """
    if entry.id is None:
        dup = variante_duplicada(db, product.id, entry.name)
        if dup is not None:
            _raise_name_conflict(dup, index)
        if entry.sku is not None:
            _ensure_sku_unique(db, entry.sku, index)
        sku = entry.sku or _unique_sku(db, f"{_slug(product.name)}-{_slug(entry.name)}")
        variant = ProductVariant(
            product_id=product.id,
            name=entry.name,
            price=entry.price,
            sku=sku,
            active=entry.active,
            display_order=_next_display_order(db, product.id),
        )
        db.add(variant)
        db.flush()
    else:
        variant = existing_by_id.get(entry.id)
        if variant is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "Variant not found",
                    "variant_index": index,
                    "variant_id": str(entry.id),
                },
            )
        if entry.sku is not None and entry.sku != variant.sku:
            _ensure_sku_unique(db, entry.sku, index, exclude_id=variant.id)
            variant.sku = entry.sku
        if entry.name != variant.name:
            dup = variante_duplicada(db, product.id, entry.name, exclude_id=variant.id)
            if dup is not None:
                _raise_name_conflict(dup, index)
            variant.name = entry.name
        variant.price = entry.price
        variant.active = entry.active
        db.flush()

    _replace_recipe(db, variant.id, entry.recipe, index)
    _replace_option_groups(db, variant.id, entry.option_groups, index)
    return variant


def _assign_display_orders(
    db: Session, product_id: UUID, resolved: list[ProductVariant]
) -> None:
    """Asigna `display_order = 1..N` a `resolved` según su posición en la lista, y reacomoda
    cualquier otra fila del mismo producto (p. ej. presentaciones inactivas que este guardado no
    tocó) por encima de ese rango.

    `UNIQUE(product_id, display_order)` aplica a **todas** las filas del producto, activas o no
    -- una presentación desactivada puede seguir ocupando un valor bajo (p. ej. 2) que el nuevo
    conjunto activo también necesita, así que no basta con reasignar solo `resolved` (a diferencia
    de `reorder_variants` arriba, que asume que el conjunto activo ya no choca con nada porque
    nunca lo reordena junto con altas/bajas en la misma llamada). Mismo patrón de dos pasadas
    (negativos primero, definitivos después), extendido a todas las filas del producto para no
    violar la constraint en ningún estado intermedio (spec 043, research.md Decisión 2). No hace
    `commit()` -- el llamador controla la transacción completa.
    """
    resolved_ids = {v.id for v in resolved}
    others = db.execute(
        select(ProductVariant).where(
            ProductVariant.product_id == product_id, ProductVariant.id.notin_(resolved_ids)
        )
    ).scalars().all()
    others.sort(key=lambda v: v.display_order)

    for i, variant in enumerate([*resolved, *others], start=1):
        variant.display_order = -i
    db.flush()

    for i, variant in enumerate(resolved, start=1):
        variant.display_order = i
    offset = len(resolved)
    for i, variant in enumerate(others, start=1):
        variant.display_order = offset + i
    db.flush()
