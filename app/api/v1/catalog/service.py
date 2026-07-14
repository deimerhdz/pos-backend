"""Servicio de catálogo (heladería): variantes vendibles y su receta (BOM).

El precio vive en la variante; la receta liga la variante a insumos de inventario.
Los grupos de opciones (sabores) se gestionan aparte y se asignan al producto.
"""
import re
from uuid import UUID

from sqlalchemy import select
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
