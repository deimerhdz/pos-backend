"""Servicio de catálogo (heladería): variantes vendibles y su receta (BOM).

El precio vive en la variante; la receta liga la variante a insumos de inventario.
Los grupos de opciones (sabores) se gestionan aparte y se asignan al producto.
"""
import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.product import Product
from app.models.product_variant import ProductVariant


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


class VariantReorderError(Exception):
    """El conjunto de `variant_ids` no coincide con las presentaciones activas del
    producto (falta alguno, sobra alguno, o hay duplicados) -- spec 042, contrato del
    endpoint de reordenamiento."""

    def __init__(self, message: str, *, missing: set[UUID] = frozenset(), extra: set[UUID] = frozenset()):
        self.missing = missing
        self.extra = extra
        super().__init__(message)


def reorder_variants(db: Session, product_id: UUID, variant_ids: list[UUID]) -> list[ProductVariant]:
    """Reasigna `display_order = 1..N` según el orden de `variant_ids` (spec 042,
    FR-002/FR-003/FR-010).

    `variant_ids` debe ser exactamente el conjunto de IDs de presentaciones ACTIVAS del
    producto, sin duplicados -- ver research.md Decisión 2 (un solo endpoint atómico,
    no un `PATCH` por presentación).

    La reasignación se hace en dos pasadas (valores negativos temporales y luego los
    definitivos) para no violar nunca `UNIQUE(product_id, display_order)` en un estado
    intermedio -- ver research.md Decisión 3, revisada durante la implementación: los
    characterization tests corren sobre SQLite, que solo permite diferir constraints
    `FOREIGN KEY`, no `UNIQUE`, así que la migración usa un `UNIQUE` simple y esta
    función evita la colisión por construcción en vez de depender de diferirla.
    """
    if len(variant_ids) != len(set(variant_ids)):
        raise VariantReorderError("variant_ids tiene IDs duplicados")

    active = db.execute(
        select(ProductVariant).where(
            ProductVariant.product_id == product_id, ProductVariant.active.is_(True)
        )
    ).scalars().all()
    by_id = {v.id: v for v in active}
    active_ids = set(by_id.keys())
    requested_ids = set(variant_ids)

    if requested_ids != active_ids:
        raise VariantReorderError(
            "variant_ids no coincide con las presentaciones activas del producto",
            missing=active_ids - requested_ids,
            extra=requested_ids - active_ids,
        )

    for i, variant_id in enumerate(variant_ids, start=1):
        by_id[variant_id].display_order = -i
    db.flush()

    for i, variant_id in enumerate(variant_ids, start=1):
        by_id[variant_id].display_order = i
    db.flush()

    db.commit()
    return [by_id[vid] for vid in variant_ids]
