"""Endpoints tenant-facing para la información del negocio (incluye logo).

El `Tenant` vive en el schema `shared`, por eso las escrituras usan
`get_shared_db` (sesión sobre shared) en vez de `get_db` (sesión del schema del
tenant). El logo se sube a R2 vía POST /uploads/presign (folder="logo") y aquí
solo se persiste su `public_url`."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_tenant
from app.core.dependencies import get_current_user, require_tenant_admin, get_shared_db
from app.core.models import Tenant, User
from app.core.storage import delete_object, key_from_public_url
from app.api.v1.tenant.schemas import TenantInfoResponse, TenantUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant", tags=["tenant"])


@router.get(
    "",
    response_model=TenantInfoResponse,
    summary="Información del negocio del tenant actual",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "Tenant no encontrado para el host."},
    },
)
def get_tenant_info(
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(get_current_user),
):
    return tenant


@router.patch(
    "",
    response_model=TenantInfoResponse,
    summary="Actualizar la información del negocio (logo)",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "El usuario no es administrador del tenant."},
    },
)
def update_tenant(
    body: TenantUpdate,
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(require_tenant_admin),
    db: Session = Depends(get_shared_db),
):
    row = db.query(Tenant).filter(Tenant.id == tenant.id).one()

    if body.logo_url is not None and body.logo_url != row.logo_url:
        old_logo_url = row.logo_url
        row.logo_url = body.logo_url
        if old_logo_url:
            old_key = key_from_public_url(old_logo_url)
            if old_key:
                delete_object(old_key)

    db.commit()
    db.refresh(row)
    return row
