"""Sesiones de mesa (staff). El comensal anónimo no entra aquí: su vía es
`/cart`, autenticada por el token de sesión firmado.

La apertura no tiene endpoint propio: una sesión de mesa nace cuando el primer
comensal escanea el QR (`POST /cart/sessions`). Aquí se consulta, se cobra y se
cierra.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db, get_tenant
from app.core.dependencies import get_current_user
from app.core.models import Tenant, User
from app.api.v1.table_sessions import service
from app.api.v1.table_sessions.schemas import (
    CloseSessionIn, CloseSessionResponse, SessionBillResponse, TableSessionResponse,
)

router = APIRouter(prefix="/table-sessions", tags=["table-sessions"])


@router.get(
    "",
    response_model=list[TableSessionResponse],
    summary="Listar sesiones de mesa",
)
def list_sessions(
    only_active: bool = Query(True, description="Solo las sesiones abiertas."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.list_sessions(db, only_active=only_active)


@router.get(
    "/{table_session_id}",
    response_model=TableSessionResponse,
    summary="Detalle de una sesión de mesa (con sus comensales)",
)
def get_session(
    table_session_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return service.get_session(db, table_session_id)


@router.get(
    "/{table_session_id}/bill",
    response_model=SessionBillResponse,
    summary="Cuenta de la sesión, con el desglose por comensal",
)
def session_bill(
    table_session_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """El desglose sale de `order_items.participant_id`, que es asignación por
    ítem: el reparto es exacto aunque un pedido mezcle comensales."""
    return service.compute_bill(db, table_session_id)


@router.post(
    "/{table_session_id}/close",
    response_model=CloseSessionResponse,
    summary="Cobrar y cerrar la sesión (billing_mode unified o split)",
)
def close_session(
    table_session_id: UUID,
    body: CloseSessionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_tenant),
):
    """Cierra en cascada: cobra, marca los pedidos `pagada`, cierra a los
    comensales, abandona sus carritos y libera la mesa. Todo en una transacción."""
    return service.close_session(
        db, table_session_id, body, user,
        invoice_prefix=tenant.invoice_prefix or "",
        tenant_id=tenant.id,
    )
