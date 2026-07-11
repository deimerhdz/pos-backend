from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.attribute import Attribute
from app.models.attribute_value import AttributeValue
from app.api.v1.attributes.schemas import (
    AttributeCreate,
    AttributeUpdate,
    AttributeResponse,
    AttributeDetailResponse,
    AttributeValueCreate,
    AttributeValueUpdate,
    AttributeValueResponse,
)

router = APIRouter(prefix="/attributes", tags=["attributes"])


@router.get("", response_model=list[AttributeDetailResponse], summary="Listar atributos con sus valores")
def list_attributes(
    active: bool | None = Query(None, description="Filtra por estado activo/inactivo."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Attribute).options(selectinload(Attribute.values)).order_by(Attribute.name)
    if active is not None:
        stmt = stmt.where(Attribute.active == active)
    return db.execute(stmt).scalars().all()


@router.get("/{id}", response_model=AttributeDetailResponse, summary="Obtener un atributo")
def get_attribute(
    id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    attr = db.execute(
        select(Attribute).options(selectinload(Attribute.values)).where(Attribute.id == id)
    ).scalar_one_or_none()
    if attr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attribute not found")
    return attr


@router.post("", response_model=AttributeDetailResponse, status_code=status.HTTP_201_CREATED, summary="Crear un atributo")
def create_attribute(
    body: AttributeCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    ensure_unique(db, Attribute, Attribute.name, body.name, "Attribute name already exists")

    attr = Attribute(name=body.name, affects_inventory=body.affects_inventory)
    db.add(attr)
    db.flush()

    for i, value in enumerate(body.values or []):
        db.add(AttributeValue(attribute_id=attr.id, value=value, sort_order=i))

    db.commit()
    return db.execute(
        select(Attribute).options(selectinload(Attribute.values)).where(Attribute.id == attr.id)
    ).scalar_one()


@router.patch("/{id}", response_model=AttributeResponse, summary="Actualizar un atributo")
def update_attribute(
    id: UUID,
    body: AttributeUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    attr = get_or_404(db, Attribute, id, "Attribute not found")

    if body.name is not None and body.name != attr.name:
        ensure_unique(db, Attribute, Attribute.name, body.name, "Attribute name already exists")
        attr.name = body.name
    if body.affects_inventory is not None:
        attr.affects_inventory = body.affects_inventory
    if body.active is not None:
        attr.active = body.active

    db.commit()
    db.refresh(attr)
    return attr


@router.post("/{id}/values", response_model=AttributeValueResponse, status_code=status.HTTP_201_CREATED, summary="Agregar un valor al atributo")
def add_value(
    id: UUID,
    body: AttributeValueCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    get_or_404(db, Attribute, id, "Attribute not found")

    dup = db.execute(
        select(AttributeValue).where(
            AttributeValue.attribute_id == id, AttributeValue.value == body.value
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Value already exists for this attribute")

    value = AttributeValue(attribute_id=id, value=body.value, sort_order=body.sort_order)
    db.add(value)
    db.commit()
    db.refresh(value)
    return value


@router.patch("/{id}/values/{value_id}", response_model=AttributeValueResponse, summary="Actualizar un valor del atributo")
def update_value(
    id: UUID,
    value_id: UUID,
    body: AttributeValueUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_tenant_admin),
):
    value = db.execute(
        select(AttributeValue).where(
            AttributeValue.id == value_id, AttributeValue.attribute_id == id
        )
    ).scalar_one_or_none()
    if value is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attribute value not found")

    if body.value is not None and body.value != value.value:
        dup = db.execute(
            select(AttributeValue).where(
                AttributeValue.attribute_id == id, AttributeValue.value == body.value
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Value already exists for this attribute")
        value.value = body.value
    if body.sort_order is not None:
        value.sort_order = body.sort_order
    if body.active is not None:
        value.active = body.active

    db.commit()
    db.refresh(value)
    return value
