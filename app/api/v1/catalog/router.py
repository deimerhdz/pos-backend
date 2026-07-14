from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.inventory_item import InventoryItem
from app.models.option_group import OptionGroup
from app.models.option import Option
from app.models.product_option_group import ProductOptionGroup
from app.api.v1.catalog.service import ensure_default_variant, _unique_sku, _slug
from app.api.v1.catalog.schemas import (
    VariantCreate,
    VariantUpdate,
    VariantResponse,
    RecipeSet,
    RecipeItemResponse,
    OptionGroupCreate,
    OptionGroupResponse,
    OptionCreate,
    OptionResponse,
    ProductOptionGroupCreate,
    ProductOptionGroupResponse,
)

router = APIRouter(tags=["catalog"])


# ============================ Variantes ============================
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
    return db.execute(
        select(ProductVariant)
        .where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.name)
    ).scalars().all()


@router.post(
    "/products/{product_id}/variants",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una variante para un producto",
)
def create_variant(
    product_id: UUID,
    body: VariantCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    product = get_or_404(db, Product, product_id, "Product not found")
    sku = body.sku or _unique_sku(db, f"{_slug(product.name)}-{_slug(body.name)}")
    if body.sku is not None:
        ensure_unique(db, ProductVariant, ProductVariant.sku, body.sku, "SKU already exists")
    variant = ProductVariant(
        product_id=product_id, name=body.name, price=body.price, sku=sku, active=True
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@router.patch(
    "/variants/{variant_id}",
    response_model=VariantResponse,
    summary="Actualizar una variante (nombre, precio, sku, activa)",
)
def update_variant(
    variant_id: UUID,
    body: VariantUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    variant = get_or_404(db, ProductVariant, variant_id, "Variant not found")
    if body.sku is not None and body.sku != variant.sku:
        ensure_unique(db, ProductVariant, ProductVariant.sku, body.sku, "SKU already exists")
        variant.sku = body.sku
    if body.name is not None:
        variant.name = body.name
    if body.price is not None:
        variant.price = body.price
    if body.active is not None:
        variant.active = body.active
    db.commit()
    db.refresh(variant)
    return variant


@router.delete(
    "/variants/{variant_id}",
    response_model=VariantResponse,
    summary="Desactivar una variante (soft-delete)",
)
def delete_variant(
    variant_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    variant = get_or_404(db, ProductVariant, variant_id, "Variant not found")
    variant.active = False
    db.commit()
    db.refresh(variant)
    return variant


# ============================ Receta (BOM) ============================
@router.get(
    "/variants/{variant_id}/recipe",
    response_model=list[RecipeItemResponse],
    summary="Ver la receta de una variante",
)
def get_recipe(
    variant_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, ProductVariant, variant_id, "Variant not found")
    return db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == variant_id)
    ).scalars().all()


@router.put(
    "/variants/{variant_id}/recipe",
    response_model=list[RecipeItemResponse],
    summary="Definir/reemplazar la receta de una variante",
)
def set_recipe(
    variant_id: UUID,
    body: RecipeSet,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, ProductVariant, variant_id, "Variant not found")
    # Reemplazo total (idempotente).
    db.execute(
        RecipeItem.__table__.delete().where(RecipeItem.product_variant_id == variant_id)
    )
    seen: set[UUID] = set()
    for it in body.items:
        if it.inventory_item_id in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Insumo repetido en la receta")
        get_or_404(db, InventoryItem, it.inventory_item_id, "Inventory item not found")
        db.add(RecipeItem(
            product_variant_id=variant_id,
            inventory_item_id=it.inventory_item_id,
            quantity=it.quantity,
        ))
        seen.add(it.inventory_item_id)
    db.commit()
    return db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == variant_id)
    ).scalars().all()


# ============================ Grupos de opciones ============================
@router.get(
    "/option-groups",
    response_model=list[OptionGroupResponse],
    summary="Listar grupos de opciones con sus opciones",
)
def list_option_groups(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.execute(
        select(OptionGroup).options(selectinload(OptionGroup.options)).order_by(OptionGroup.name)
    ).scalars().all()


@router.post(
    "/option-groups",
    response_model=OptionGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un grupo de opciones",
)
def create_option_group(
    body: OptionGroupCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    if body.max_select < body.min_select:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_select < min_select")
    ensure_unique(db, OptionGroup, OptionGroup.name, body.name, "Option group name already exists")
    group = OptionGroup(name=body.name, min_select=body.min_select, max_select=body.max_select)
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


@router.post(
    "/option-groups/{group_id}/options",
    response_model=OptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar una opción (p.ej. un sabor) a un grupo",
)
def add_option(
    group_id: UUID,
    body: OptionCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, OptionGroup, group_id, "Option group not found")
    if body.inventory_item_id is not None:
        get_or_404(db, InventoryItem, body.inventory_item_id, "Inventory item not found")
    dup = db.execute(
        select(Option).where(Option.option_group_id == group_id, Option.name == body.name)
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Option name already exists in group")
    option = Option(
        option_group_id=group_id,
        name=body.name,
        extra_price=body.extra_price,
        inventory_item_id=body.inventory_item_id,
        item_quantity=body.item_quantity,
    )
    db.add(option)
    db.commit()
    db.refresh(option)
    return option


# ============================ Asignación grupo<->producto ============================
@router.post(
    "/products/{product_id}/option-groups",
    response_model=ProductOptionGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Asignar un grupo de opciones a un producto",
)
def assign_option_group(
    product_id: UUID,
    body: ProductOptionGroupCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Product, product_id, "Product not found")
    get_or_404(db, OptionGroup, body.option_group_id, "Option group not found")
    dup = db.execute(
        select(ProductOptionGroup).where(
            ProductOptionGroup.product_id == product_id,
            ProductOptionGroup.option_group_id == body.option_group_id,
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Option group already assigned")
    link = ProductOptionGroup(
        product_id=product_id,
        option_group_id=body.option_group_id,
        min_select=body.min_select,
        max_select=body.max_select,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
