from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.product import Product
from app.models.attribute import Attribute
from app.models.product_attribute import ProductAttribute
from app.models.variant import Variant
from app.models.variant_value import VariantValue
from app.models.modifier_group import ModifierGroup
from app.models.product_modifier_group import ProductModifierGroup
from app.api.v1.catalog.service import generate_variants
from app.api.v1.catalog.schemas import (
    ProductAttributeAssign,
    ProductAttributeResponse,
    VariantResponse,
    VariantUpdate,
    VariantGenerateResponse,
    ProductModifierGroupAssign,
    ProductModifierGroupResponse,
)

router = APIRouter(tags=["catalog"])


def _load_variants(db: Session, product_id: UUID) -> list[Variant]:
    return db.execute(
        select(Variant)
        .options(selectinload(Variant.values).selectinload(VariantValue.attribute_value))
        .where(Variant.product_id == product_id)
        .order_by(Variant.sku)
    ).scalars().all()


@router.post(
    "/products/{product_id}/attributes",
    response_model=list[ProductAttributeResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Asignar atributos a un producto configurable",
)
def assign_attributes(
    product_id: UUID,
    body: ProductAttributeAssign,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Product, product_id, "Product not found")

    existing = set(
        db.execute(
            select(ProductAttribute.attribute_id).where(
                ProductAttribute.product_id == product_id
            )
        ).scalars().all()
    )
    for attr_id in body.attribute_ids:
        if attr_id in existing:
            continue
        get_or_404(db, Attribute, attr_id, f"Attribute {attr_id} not found")
        db.add(ProductAttribute(product_id=product_id, attribute_id=attr_id))
        existing.add(attr_id)

    db.commit()
    return db.execute(
        select(ProductAttribute).where(ProductAttribute.product_id == product_id)
    ).scalars().all()


@router.get(
    "/products/{product_id}/variants",
    response_model=list[VariantResponse],
    summary="Listar las variantes de un producto",
)
def list_variants(
    product_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, Product, product_id, "Product not found")
    return _load_variants(db, product_id)


@router.post(
    "/products/{product_id}/variants:generate",
    response_model=VariantGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generar variantes (producto cartesiano de atributos)",
)
def generate_product_variants(
    product_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    product = get_or_404(db, Product, product_id, "Product not found")
    created = generate_variants(db, product)
    variants = _load_variants(db, product_id)
    return {"created": len(created), "total": len(variants), "variants": variants}


@router.patch(
    "/variants/{variant_id}",
    response_model=VariantResponse,
    summary="Actualizar una variante (sku, precio, activa)",
)
def update_variant(
    variant_id: UUID,
    body: VariantUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    variant = get_or_404(db, Variant, variant_id, "Variant not found")

    # Regla: un producto configurable no puede quedar sin ninguna variante activa.
    if body.active is False and variant.active:
        product = db.get(Product, variant.product_id)
        if product is not None and product.type == "CONFIGURABLE":
            active_count = db.execute(
                select(func.count())
                .select_from(Variant)
                .where(Variant.product_id == product.id, Variant.active == True)
            ).scalar_one()
            if active_count <= 1:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "Un producto configurable debe tener al menos una variante activa.",
                )

    if body.sku is not None and body.sku != variant.sku:
        ensure_unique(db, Variant, Variant.sku, body.sku, "SKU already exists")
        variant.sku = body.sku
    if body.price is not None:
        variant.price = body.price
    if body.active is not None:
        variant.active = body.active

    db.commit()
    return db.execute(
        select(Variant)
        .options(selectinload(Variant.values).selectinload(VariantValue.attribute_value))
        .where(Variant.id == variant_id)
    ).scalar_one()


@router.post(
    "/products/{product_id}/modifier-groups",
    response_model=ProductModifierGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Asociar un grupo de modificadores a un producto",
)
def assign_modifier_group(
    product_id: UUID,
    body: ProductModifierGroupAssign,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Product, product_id, "Product not found")
    get_or_404(db, ModifierGroup, body.group_id, "Modifier group not found")

    dup = db.execute(
        select(ProductModifierGroup).where(
            ProductModifierGroup.product_id == product_id,
            ProductModifierGroup.group_id == body.group_id,
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Group already assigned to product")

    link = ProductModifierGroup(product_id=product_id, group_id=body.group_id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
