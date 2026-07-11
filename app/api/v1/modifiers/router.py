from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.modifier_group import ModifierGroup
from app.models.modifier import Modifier
from app.api.v1.supplies.consumption import resolve_recipe
from app.api.v1.modifiers.schemas import (
    ModifierGroupCreate,
    ModifierGroupUpdate,
    ModifierGroupResponse,
    ModifierGroupDetailResponse,
    ModifierCreate,
    ModifierUpdate,
    ModifierResponse,
)

router = APIRouter(prefix="/modifier-groups", tags=["modifiers"])


def _validate_rules(required: bool, min_select: int, max_select: int | None) -> None:
    if max_select is not None and min_select > max_select:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "min_select cannot be greater than max_select")
    if required and min_select < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a required group must have min_select >= 1")


@router.get("", response_model=list[ModifierGroupDetailResponse], summary="Listar grupos de modificadores")
def list_groups(
    active: bool | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(ModifierGroup).options(selectinload(ModifierGroup.modifiers)).order_by(ModifierGroup.name)
    if active is not None:
        stmt = stmt.where(ModifierGroup.active == active)
    return db.execute(stmt).scalars().all()


@router.get("/{id}", response_model=ModifierGroupDetailResponse, summary="Obtener un grupo de modificadores")
def get_group(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    group = db.execute(
        select(ModifierGroup).options(selectinload(ModifierGroup.modifiers)).where(ModifierGroup.id == id)
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modifier group not found")
    return group


@router.post("", response_model=ModifierGroupDetailResponse, status_code=status.HTTP_201_CREATED, summary="Crear un grupo de modificadores")
def create_group(
    body: ModifierGroupCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    group = ModifierGroup(
        name=body.name,
        required=body.required,
        min_select=body.min_select,
        max_select=body.max_select,
    )
    db.add(group)
    db.flush()

    for m in body.modifiers or []:
        db.add(Modifier(group_id=group.id, name=m.name, price=m.price, active=False))

    db.commit()
    return db.execute(
        select(ModifierGroup).options(selectinload(ModifierGroup.modifiers)).where(ModifierGroup.id == group.id)
    ).scalar_one()


@router.patch("/{id}", response_model=ModifierGroupResponse, summary="Actualizar un grupo de modificadores")
def update_group(
    id: UUID,
    body: ModifierGroupUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    group = get_or_404(db, ModifierGroup, id, "Modifier group not found")

    required = body.required if body.required is not None else group.required
    min_select = body.min_select if body.min_select is not None else group.min_select
    max_select = body.max_select if body.max_select is not None else group.max_select
    _validate_rules(required, min_select, max_select)

    if body.name is not None:
        group.name = body.name
    group.required = required
    group.min_select = min_select
    group.max_select = max_select
    if body.active is not None:
        group.active = body.active

    db.commit()
    db.refresh(group)
    return group


@router.post("/{id}/modifiers", response_model=ModifierResponse, status_code=status.HTTP_201_CREATED, summary="Agregar un modificador al grupo")
def add_modifier(
    id: UUID,
    body: ModifierCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, ModifierGroup, id, "Modifier group not found")
    modifier = Modifier(group_id=id, name=body.name, price=body.price, active=False)
    db.add(modifier)
    db.commit()
    db.refresh(modifier)
    return modifier


@router.patch("/{id}/modifiers/{modifier_id}", response_model=ModifierResponse, summary="Actualizar un modificador")
def update_modifier(
    id: UUID,
    modifier_id: UUID,
    body: ModifierUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    modifier = db.execute(
        select(Modifier).where(Modifier.id == modifier_id, Modifier.group_id == id)
    ).scalar_one_or_none()
    if modifier is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modifier not found")

    # Regla (Fase 1): no se puede activar un modificador sin receta activa con items.
    if body.active is True and not modifier.active:
        recipe = resolve_recipe(db, modifier_id=modifier_id)
        if recipe is None or not recipe.items:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "No se puede activar un modificador sin receta activa. Define su receta primero.",
            )

    if body.name is not None:
        modifier.name = body.name
    if body.price is not None:
        modifier.price = body.price
    if body.active is not None:
        modifier.active = body.active

    db.commit()
    db.refresh(modifier)
    return modifier
