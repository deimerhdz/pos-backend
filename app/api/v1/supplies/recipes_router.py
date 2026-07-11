from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.variant import Variant
from app.models.modifier import Modifier
from app.models.recipe import Recipe
from app.models.recipe_item import RecipeItem
from app.models.supply import Supply
from app.models.unit_measure import UnitMeasure
from app.api.v1.supplies.schemas import RecipeUpsert, RecipeResponse

router = APIRouter(tags=["recipes"])


def _load_recipe(db: Session, recipe_id: UUID) -> Recipe:
    return db.execute(
        select(Recipe)
        .options(
            selectinload(Recipe.items).selectinload(RecipeItem.supply),
            selectinload(Recipe.items).selectinload(RecipeItem.unit_measure),
        )
        .where(Recipe.id == recipe_id)
    ).scalar_one()


def _upsert_recipe(db: Session, body: RecipeUpsert, *, variant_id=None, modifier_id=None) -> Recipe:
    owner_clause = (
        Recipe.variant_id == variant_id if variant_id is not None else Recipe.modifier_id == modifier_id
    )
    recipe = db.execute(select(Recipe).where(owner_clause)).scalar_one_or_none()
    if recipe is None:
        recipe = Recipe(variant_id=variant_id, modifier_id=modifier_id)
        db.add(recipe)
        db.flush()
    else:
        for item in list(recipe.items):
            db.delete(item)
        db.flush()

    recipe.is_resale = body.is_resale

    if body.is_resale and len(body.items) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Una receta de reventa (1:1) debe tener exactamente un insumo.",
        )

    for it in body.items:
        supply = get_or_404(db, Supply, it.supply_id, f"Supply {it.supply_id} not found")
        if body.is_resale:
            # Reventa 1:1: cantidad 1, unidad = unidad base del insumo (sin conversión).
            db.add(RecipeItem(
                recipe_id=recipe.id,
                supply_id=it.supply_id,
                quantity=Decimal(1),
                unit_measure_id=supply.unit_measure_id,
            ))
            continue
        unit = get_or_404(db, UnitMeasure, it.unit_measure_id, f"Unit measure {it.unit_measure_id} not found")
        base_unit = supply.unit_measure
        if base_unit is not None and unit.dimension != base_unit.dimension:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unidad '{unit.abbreviation}' ({unit.dimension}) incompatible con el insumo "
                f"'{supply.name}' ({base_unit.dimension}).",
            )
        db.add(RecipeItem(
            recipe_id=recipe.id,
            supply_id=it.supply_id,
            quantity=it.quantity,
            unit_measure_id=it.unit_measure_id,
        ))

    db.commit()
    return _load_recipe(db, recipe.id)


def _get_recipe(db: Session, *, variant_id=None, modifier_id=None) -> Recipe:
    owner_clause = (
        Recipe.variant_id == variant_id if variant_id is not None else Recipe.modifier_id == modifier_id
    )
    recipe = db.execute(select(Recipe).where(owner_clause)).scalar_one_or_none()
    if recipe is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recipe not found")
    return _load_recipe(db, recipe.id)


@router.put("/variants/{variant_id}/recipe", response_model=RecipeResponse, summary="Definir/reemplazar la receta de una variante")
def put_variant_recipe(
    variant_id: UUID,
    body: RecipeUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Variant, variant_id, "Variant not found")
    return _upsert_recipe(db, body, variant_id=variant_id)


@router.get("/variants/{variant_id}/recipe", response_model=RecipeResponse, summary="Obtener la receta de una variante")
def get_variant_recipe(
    variant_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, Variant, variant_id, "Variant not found")
    return _get_recipe(db, variant_id=variant_id)


@router.put("/modifiers/{modifier_id}/recipe", response_model=RecipeResponse, summary="Definir/reemplazar la receta de un modificador")
def put_modifier_recipe(
    modifier_id: UUID,
    body: RecipeUpsert,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Modifier, modifier_id, "Modifier not found")
    return _upsert_recipe(db, body, modifier_id=modifier_id)


@router.get("/modifiers/{modifier_id}/recipe", response_model=RecipeResponse, summary="Obtener la receta de un modificador")
def get_modifier_recipe(
    modifier_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    get_or_404(db, Modifier, modifier_id, "Modifier not found")
    return _get_recipe(db, modifier_id=modifier_id)
