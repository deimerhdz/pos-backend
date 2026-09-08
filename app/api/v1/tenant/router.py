"""Endpoints tenant-facing para la información del negocio (incluye logo).

El `Tenant` vive en el schema `shared`, por eso las escrituras usan
`get_shared_db` (sesión sobre shared) en vez de `get_db` (sesión del schema del
tenant). El logo se sube a R2 vía POST /uploads/presign (folder="logo"); spec
080: en base de datos vive solo la `key` del objeto (el esquema normaliza a key
cualquier URL absoluta del bucket gestionado que llegue, FR-004) y la URL de
visualización se arma al responder contra `ASSETS_BASE_URL`."""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_tenant
from app.core.dependencies import get_current_user, require_tenant_admin, get_shared_db
from app.core.models import Tenant, User
from app.core.storage import delete_object, object_key_for_deletion
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
    summary="Actualizar la información del negocio (logo, recibo, prefijo de factura)",
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

    old_logo_key = None
    # spec 080: `body.logo_url` ya viene normalizada a key (AssetRefIn) — la
    # comparación es key vs key, así que reenviar la URL de visualización de un
    # formulario que no tocó el logo no cuenta como cambio (US3, FR-011/FR-012).
    if body.logo_url is not None and body.logo_url != row.logo_url:
        # None si la referencia anterior es de otro origen (FR-013): no se borra.
        old_logo_key = object_key_for_deletion(row.logo_url)
        row.logo_url = body.logo_url

    if body.receipt_message is not None:
        # Vaciar el campo es la forma de quitar el mensaje del recibo.
        row.receipt_message = body.receipt_message.strip() or None

    if body.invoice_prefix is not None:
        # Cambiarlo arranca una numeración nueva: cada prefijo lleva su propio
        # consecutivo, así que el nuevo empieza en 1 y el anterior se conserva.
        row.invoice_prefix = body.invoice_prefix.strip().upper() or None

    db.commit()
    db.refresh(row)
    # A-44 / spec 021 (intacto): primero confirmar, después borrar best-effort.
    if old_logo_key:
        delete_object(old_logo_key)
    return row
