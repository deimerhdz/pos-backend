"""Servicio de catálogo (Fase 1): generación de variantes por producto cartesiano."""
import itertools
import re
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.product import Product
from app.models.product_attribute import ProductAttribute
from app.models.attribute_value import AttributeValue
from app.models.variant import Variant
from app.models.variant_value import VariantValue


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]+", "", (text or "").upper())
    return cleaned[:4] or "X"


def _unique_sku(db: Session, base: str) -> str:
    sku = base
    i = 2
    while db.execute(select(Variant.id).where(Variant.sku == sku)).first() is not None:
        sku = f"{base}-{i}"
        i += 1
    return sku


def ensure_default_variant(db: Session, product: Product) -> Variant | None:
    """Garantiza que un producto tenga una variante default (para poder venderse).
    Usada por productos SIMPLE, que no pasan por la generación cartesiana."""
    existing = db.execute(
        select(Variant).where(Variant.product_id == product.id).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    variant = Variant(
        product_id=product.id,
        sku=_unique_sku(db, f"{_slug(product.name)}-DEF"),
        price=product.price if product.price is not None else 0,
        is_default=True,
        active=False,  # Fase 1: no vendible hasta tener receta (reventa 1:1 para SIMPLE).
    )
    db.add(variant)
    db.flush()
    return variant


def generate_variants(db: Session, product: Product) -> list[Variant]:
    """Genera el producto cartesiano de los valores activos de los atributos
    asignados al producto. Idempotente: no duplica combinaciones existentes."""
    attr_ids = db.execute(
        select(ProductAttribute.attribute_id).where(
            ProductAttribute.product_id == product.id
        )
    ).scalars().all()
    if not attr_ids:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "El producto no tiene atributos asignados.",
        )

    value_lists: list[list[AttributeValue]] = []
    for aid in attr_ids:
        values = db.execute(
            select(AttributeValue)
            .where(AttributeValue.attribute_id == aid, AttributeValue.active == True)
            .order_by(AttributeValue.sort_order)
        ).scalars().all()
        if not values:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Un atributo asignado no tiene valores activos.",
            )
        value_lists.append(values)

    existing = db.execute(
        select(Variant)
        .options(selectinload(Variant.values))
        .where(Variant.product_id == product.id)
    ).scalars().all()
    existing_keys = {frozenset(vv.attribute_value_id for vv in v.values) for v in existing}

    prod_slug = _slug(product.name)
    initial_price = product.price if product.price is not None else 0
    created: list[Variant] = []

    for combo in itertools.product(*value_lists):
        key = frozenset(av.id for av in combo)
        if key in existing_keys:
            continue
        base_sku = "-".join([prod_slug] + [_slug(av.value) for av in combo])
        variant = Variant(
            product_id=product.id,
            sku=_unique_sku(db, base_sku),
            price=initial_price,
            active=False,  # Fase 1: nace inactiva; se activa al tener receta.
        )
        db.add(variant)
        db.flush()
        for av in combo:
            db.add(VariantValue(variant_id=variant.id, attribute_value_id=av.id))
        existing_keys.add(key)
        created.append(variant)

    db.commit()
    return created
