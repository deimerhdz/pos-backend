from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.presentation import Presentation
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.recipe_item import RecipeItem
from app.models.inventory_item import InventoryItem
from app.models.option_group import OptionGroup
from app.models.option import Option
from app.models.variant_option_group import VariantOptionGroup
from app.api.v1.catalog.service import (
    ensure_default_variant,
    variante_duplicada,
    _unique_sku,
    _slug,
    _next_display_order,
    reorder_variants,
    VariantReorderError,
)
from app.api.v1.catalog.schemas import (
    VariantCreate,
    VariantUpdate,
    VariantResponse,
    VariantReorderRequest,
    VariantReorderResponse,
    RecipeSet,
    RecipeItemResponse,
    OptionGroupCreate,
    OptionGroupUpdate,
    OptionGroupResponse,
    OptionCreate,
    OptionUpdate,
    OptionResponse,
    VariantOptionGroupSet,
    VariantOptionGroupResponse,
)

router = APIRouter(tags=["catalog"])


# ============================ Variantes ============================
def _bloquear_nombre_duplicado(
    db: Session, product_id: UUID, name: str, *, exclude_id: UUID | None = None
) -> None:
    """409 si otra variante del producto ya ocupa ese nombre.

    Devuelve `variant_id` y `active` en el detalle para que el editor pueda ofrecer
    «reactivar esta presentación» (un `PATCH {active: true}`) cuando la que estorba es
    una desactivada, que el frontend no lista y por eso el usuario intenta recrear.
    """
    dup = variante_duplicada(db, product_id, name, exclude_id=exclude_id)
    if dup is None:
        return
    if dup.active:
        mensaje = f"Ya existe una variante «{dup.name}» en este producto"
    else:
        mensaje = (
            f"Ya existe una variante «{dup.name}» desactivada en este producto. "
            "Reactívala en vez de crear otra."
        )
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={"error": mensaje, "variant_id": str(dup.id), "active": dup.active},
    )


def _resolve_presentation_id(db: Session, presentation_id: UUID | None) -> UUID | None:
    """spec 040: `presentation_id` debe referenciar una presentación existente y
    ACTIVA, o ser NULL (FR-008). Cambiar la presentación de una variante NO
    dispara la verificación de uniformidad de FR-017 (esa corre solo al guardar
    una regla de promoción, CL-1b)."""
    if presentation_id is None:
        return None
    presentation = db.get(Presentation, presentation_id)
    if presentation is None or not presentation.active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Presentación no encontrada o inactiva",
        )
    return presentation.id


def _commit_variante(db: Session, variant: ProductVariant) -> ProductVariant:
    """Commit + refresh traduciendo el choque de la constraint única a 409.

    La comprobación previa no puede cerrar la carrera entre dos admins guardando a la
    vez; sin esto, esa carrera sale como 500."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ya existe una variante con ese nombre o SKU",
        )
    db.refresh(variant)
    return variant


@router.get(
    "/products/{product_id}/variants",
    response_model=list[VariantResponse],
    summary="Listar las variantes de un producto",
    responses={404: {"description": "El producto no existe."}},
)
def list_variants(
    product_id: UUID,
    active: bool | None = Query(None, description="Filtra por estado activo/inactivo."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, Product, product_id, "Product not found")
    stmt = select(ProductVariant).where(ProductVariant.product_id == product_id)
    if active is not None:
        stmt = stmt.where(ProductVariant.active.is_(active))
    # spec 042: antes ordenaba por nombre (alfabético) -- ahora refleja el orden que el
    # administrador definió (o el de creación, por backfill, si nunca lo cambió).
    return db.execute(stmt.order_by(ProductVariant.display_order)).scalars().all()


@router.post(
    "/products/{product_id}/variants",
    response_model=VariantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una variante para un producto",
    responses={
        404: {"description": "El producto no existe."},
        409: {"description": "Ya existe una variante con ese nombre (activa o desactivada) o con ese SKU."},
    },
)
def create_variant(
    product_id: UUID,
    body: VariantCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    product = get_or_404(db, Product, product_id, "Product not found")
    name = body.name  # ya viene recortado por el schema
    _bloquear_nombre_duplicado(db, product_id, name)
    if body.sku is not None:
        ensure_unique(db, ProductVariant, ProductVariant.sku, body.sku, "SKU already exists")
    sku = body.sku or _unique_sku(db, f"{_slug(product.name)}-{_slug(name)}")
    variant = ProductVariant(
        product_id=product_id,
        name=name,
        price=body.price,
        sku=sku,
        active=True,
        display_order=_next_display_order(db, product_id),
        presentation_id=_resolve_presentation_id(db, body.presentation_id),
    )
    db.add(variant)
    return _commit_variante(db, variant)


@router.patch(
    "/products/{product_id}/variants/reorder",
    response_model=VariantReorderResponse,
    summary="Reordenar las presentaciones activas de un producto",
    responses={
        404: {"description": "El producto no existe."},
        422: {"description": "variant_ids no coincide exactamente con las presentaciones activas del producto."},
    },
)
def reorder_product_variants(
    product_id: UUID,
    body: VariantReorderRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Product, product_id, "Product not found")
    try:
        variants = reorder_variants(db, product_id, body.variant_ids)
    except VariantReorderError as exc:
        # La validación de reorder_variants ocurre antes de tocar ninguna fila --
        # no hay nada que revertir; no se llama rollback() aquí a propósito.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": str(exc),
                "missing": [str(v) for v in exc.missing],
                "extra": [str(v) for v in exc.extra],
            },
        )
    return VariantReorderResponse(
        variants=[{"id": v.id, "display_order": v.display_order} for v in variants]
    )


@router.patch(
    "/variants/{variant_id}",
    response_model=VariantResponse,
    summary="Actualizar una variante (nombre, precio, sku, activa)",
    responses={
        404: {"description": "La variante no existe."},
        409: {"description": "Otra variante del producto ya usa ese nombre, o el SKU está tomado."},
    },
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
    if body.name is not None and body.name != variant.name:
        _bloquear_nombre_duplicado(
            db, variant.product_id, body.name, exclude_id=variant_id
        )
        variant.name = body.name
    if body.price is not None:
        variant.price = body.price
    if body.active is not None:
        variant.active = body.active
    if "presentation_id" in body.model_fields_set:
        variant.presentation_id = _resolve_presentation_id(db, body.presentation_id)
    return _commit_variante(db, variant)


@router.delete(
    "/variants/{variant_id}",
    response_model=VariantResponse,
    summary="Desactivar una variante (soft-delete)",
    responses={404: {"description": "La variante no existe."}},
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
    vistos: set[UUID] = set()
    for it in body.items:
        if it.inventory_item_id in vistos:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Insumo repetido en la receta"
            )
        get_or_404(db, InventoryItem, it.inventory_item_id, "Inventory item not found")
        vistos.add(it.inventory_item_id)
        db.add(RecipeItem(
            product_variant_id=variant_id,
            inventory_item_id=it.inventory_item_id,
            quantity=it.quantity,
        ))
    db.commit()
    return db.execute(
        select(RecipeItem).where(RecipeItem.product_variant_id == variant_id)
    ).scalars().all()


# ================= Grupos de opciones de una variante =================
@router.get(
    "/variants/{variant_id}/option-groups",
    response_model=list[VariantOptionGroupResponse],
    summary="Grupos de opciones que ofrece una variante",
)
def get_variant_option_groups(
    variant_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, ProductVariant, variant_id, "Variant not found")
    return db.execute(
        select(VariantOptionGroup).where(
            VariantOptionGroup.product_variant_id == variant_id
        )
    ).scalars().all()


@router.put(
    "/variants/{variant_id}/option-groups",
    response_model=list[VariantOptionGroupResponse],
    summary="Definir/reemplazar los grupos de opciones de una variante",
)
def set_variant_option_groups(
    variant_id: UUID,
    body: VariantOptionGroupSet,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    """Reemplazo total e idempotente, igual que la receta.

    Cada fila dice «esta presentación ofrece este grupo: elige N–M, descuenta X de cada
    opción elegida». Es lo que permite que la ensalada pequeña elija 1 sabor de 60 g y
    la mediana 2 de 120 g sin duplicar el grupo ni el producto.
    """
    get_or_404(db, ProductVariant, variant_id, "Variant not found")

    db.execute(
        VariantOptionGroup.__table__.delete().where(
            VariantOptionGroup.product_variant_id == variant_id
        )
    )
    vistos: set[UUID] = set()
    for g in body.groups:
        if g.option_group_id in vistos:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Grupo de opciones repetido en esta presentación",
            )
        group = get_or_404(db, OptionGroup, g.option_group_id, "Option group not found")
        if not group.active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El grupo «{group.name}» está inactivo",
            )
        vistos.add(g.option_group_id)
        db.add(VariantOptionGroup(
            product_variant_id=variant_id,
            option_group_id=g.option_group_id,
            min_select=g.min_select,
            max_select=g.max_select,
            quantity_per_option=g.quantity_per_option,
        ))
    db.commit()
    return db.execute(
        select(VariantOptionGroup).where(
            VariantOptionGroup.product_variant_id == variant_id
        )
    ).scalars().all()


# ============================ Grupos de opciones ============================
def _variantes_que_lo_usan(db: Session, group_id: UUID) -> list[str]:
    """Variantes que ofrecen este grupo, como 'Producto · Variante'.

    Retirarlo las dejaría ofreciendo algo que el cliente ya no puede elegir, o —peor—
    vendiendo sin descontar. Se bloquea en vez de borrar en cascada, que es exactamente
    el tipo de fallo silencioso que costó caro antes."""
    stmt = (
        select(Product.name, ProductVariant.name)
        .join(ProductVariant, ProductVariant.product_id == Product.id)
        .join(VariantOptionGroup, VariantOptionGroup.product_variant_id == ProductVariant.id)
        .where(VariantOptionGroup.option_group_id == group_id)
    )
    return [f"{p} · {v}" for p, v in db.execute(stmt).all()]


def _bloquear_si_esta_en_uso(db: Session, group_id: UUID, accion: str) -> None:
    variantes = _variantes_que_lo_usan(db, group_id)
    if not variantes:
        return
    nombres = ", ".join(f"«{v}»" for v in dict.fromkeys(variantes))
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": (
                f"No se puede {accion}: {nombres} lo ofrece a sus clientes. "
                "Quítalo de esas presentaciones primero."
            ),
            "variantes_en_uso": list(dict.fromkeys(variantes)),
        },
    )


@router.get(
    "/option-groups",
    response_model=list[OptionGroupResponse],
    summary="Listar grupos de opciones con sus opciones",
)
def list_option_groups(
    active: bool | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(OptionGroup).options(selectinload(OptionGroup.options))
    if active is not None:
        stmt = stmt.where(OptionGroup.active.is_(active))
    return db.execute(stmt.order_by(OptionGroup.name)).scalars().all()


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


@router.patch(
    "/option-groups/{group_id}",
    response_model=OptionGroupResponse,
    summary="Actualizar un grupo de opciones (nombre, min/max, activo)",
)
def update_option_group(
    group_id: UUID,
    body: OptionGroupUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    group = get_or_404(db, OptionGroup, group_id, "Option group not found")
    if body.name is not None and body.name != group.name:
        ensure_unique(
            db, OptionGroup, OptionGroup.name, body.name,
            "Option group name already exists", exclude_id=group_id,
        )
        group.name = body.name
    min_select = body.min_select if body.min_select is not None else group.min_select
    max_select = body.max_select if body.max_select is not None else group.max_select
    if max_select < min_select:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "max_select < min_select")
    group.min_select = min_select
    group.max_select = max_select
    if body.active is not None:
        if not body.active and group.active:
            _bloquear_si_esta_en_uso(db, group_id, "desactivar este grupo")
        group.active = body.active
    db.commit()
    db.refresh(group)
    return group


@router.delete(
    "/option-groups/{group_id}",
    response_model=OptionGroupResponse,
    summary="Desactivar un grupo de opciones (soft-delete)",
)
def delete_option_group(
    group_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    group = get_or_404(db, OptionGroup, group_id, "Option group not found")
    _bloquear_si_esta_en_uso(db, group_id, "desactivar este grupo")
    group.active = False
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


@router.patch(
    "/options/{option_id}",
    response_model=OptionResponse,
    summary="Actualizar una opción (nombre, precio extra, insumo, activa)",
)
def update_option(
    option_id: UUID,
    body: OptionUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    option = get_or_404(db, Option, option_id, "Option not found")
    if body.name is not None and body.name != option.name:
        dup = db.execute(
            select(Option).where(
                Option.option_group_id == option.option_group_id,
                Option.name == body.name,
                Option.id != option_id,
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Option name already exists in group")
        option.name = body.name
    if body.extra_price is not None:
        option.extra_price = body.extra_price
    # `None` explícito desliga el insumo; ausente = no tocar.
    if "inventory_item_id" in body.model_fields_set:
        if body.inventory_item_id is not None:
            get_or_404(db, InventoryItem, body.inventory_item_id, "Inventory item not found")
            option.inventory_item_id = body.inventory_item_id
        else:
            option.inventory_item_id = None
            option.item_quantity = 0
    if body.item_quantity is not None and option.inventory_item_id is not None:
        option.item_quantity = body.item_quantity
    if body.active is not None:
        option.active = body.active
    db.commit()
    db.refresh(option)
    return option


@router.delete(
    "/options/{option_id}",
    response_model=OptionResponse,
    summary="Desactivar una opción (soft-delete: conserva el histórico de ventas)",
)
def delete_option(
    option_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    option = get_or_404(db, Option, option_id, "Option not found")
    option.active = False
    db.commit()
    db.refresh(option)
    return option


# La asignación grupo<->producto se retiró: los grupos cuelgan de la VARIANTE, vía
# `GET`/`PUT /variants/{id}/option-groups` (más arriba). Un solo `PUT` idempotente por
# presentación sustituye al par assign/unassign, y con él desaparece el orden obligado
# que tenía que respetar el editor al guardar.
