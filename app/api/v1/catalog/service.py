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
    )
    db.add(variant)
    db.flush()
    return variant
