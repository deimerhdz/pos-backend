from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_shared_db
from app.core.crud import ensure_unique, get_or_404
from app.models.payment_method_catalog import PaymentMethodCatalog
from app.api.v1.super_admin.schemas import (
    PaymentMethodCatalogCreate,
    PaymentMethodCatalogResponse,
    PaymentMethodCatalogUpdate,
)

router = APIRouter(prefix="/payment-methods-catalog", tags=["super-admin"])


@router.get(
    "",
    response_model=list[PaymentMethodCatalogResponse],
    summary="Listar el catálogo de métodos de pago (completo)",
    description="Devuelve el catálogo completo, activos e inactivos. Solo el super admin.",
)
def list_payment_method_catalog(db: Session = Depends(get_shared_db)):
    return db.execute(
        select(PaymentMethodCatalog).order_by(PaymentMethodCatalog.name)
    ).scalars().all()


@router.post(
    "",
    response_model=PaymentMethodCatalogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un método de pago en el catálogo",
    description="Crea una entrada de catálogo, disponible de inmediato para que los tenants la activen (FR-001).",
    responses={409: {"description": "Ya existe un método de catálogo con ese nombre."}},
)
def create_payment_method_catalog(
    body: PaymentMethodCatalogCreate, db: Session = Depends(get_shared_db),
):
    ensure_unique(
        db, PaymentMethodCatalog, PaymentMethodCatalog.name, body.name,
        "Ya existe un método de pago en el catálogo con ese nombre",
    )
    entry = PaymentMethodCatalog(
        name=body.name,
        type=body.type.value,
        fields=[f.model_dump(exclude_none=True) for f in body.fields],
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.patch(
    "/{catalog_id}",
    response_model=PaymentMethodCatalogResponse,
    summary="Editar/activar/desactivar un método de pago del catálogo",
    description="Edita nombre/tipo/campos y/o activa-desactiva a nivel plataforma (FR-002/FR-003).",
    responses={
        404: {"description": "No existe esa entrada de catálogo."},
        409: {"description": "El nuevo nombre colisiona con otra entrada."},
    },
)
def update_payment_method_catalog(
    catalog_id: UUID, body: PaymentMethodCatalogUpdate, db: Session = Depends(get_shared_db),
):
    entry = get_or_404(db, PaymentMethodCatalog, catalog_id)

    if body.name is not None and body.name != entry.name:
        ensure_unique(
            db, PaymentMethodCatalog, PaymentMethodCatalog.name, body.name,
            "Ya existe un método de pago en el catálogo con ese nombre",
            exclude_id=entry.id,
        )
        entry.name = body.name

    if body.type is not None:
        entry.type = body.type.value

    if body.fields is not None:
        entry.fields = [f.model_dump(exclude_none=True) for f in body.fields]

    if body.active is not None:
        entry.active = body.active

    db.commit()
    db.refresh(entry)
    return entry
