"""Contexto público del comensal a partir de un token de QR firmado.

Reemplaza la resolución por header x-tenant-host en el flujo público: el token
firmado lleva tenant + mesa, así que resolvemos el schema del tenant y abrimos
una sesión ya scopeada, sin exponer el table_id plano ni confiar en headers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator
from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import resolve_tenant_by_id, with_db
from app.core.models import Tenant
from app.core.qr_token import (
    QrTokenError,
    SessionExpiredError,
    SessionInvalidError,
    verify_qr_token,
    verify_session_token,
)


@dataclass
class QrContext:
    db: Session
    tenant: Tenant
    table_id: UUID


@contextmanager
def open_qr_context(token: str) -> Iterator[QrContext]:
    """Verifica el token de QR, resuelve el tenant y abre una sesión scopeada a
    su schema. Lanza 401 si el token es inválido/manipulado."""
    try:
        claims = verify_qr_token(token)
    except QrTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de QR inválido"
        )
    tenant = resolve_tenant_by_id(claims.tenant_id)
    with with_db(tenant.schema) as db:
        yield QrContext(db=db, tenant=tenant, table_id=claims.table_id)


def get_qr_context(x_qr_token: str = Header(..., alias="x-qr-token")) -> Iterator[QrContext]:
    """Dependencia FastAPI: lee el token del header `x-qr-token` y entrega el
    contexto del comensal (db tenant-scoped + mesa)."""
    with open_qr_context(x_qr_token) as ctx:
        yield ctx


# --------------------------------------------------------------- Sesión (carrito)

@dataclass
class SessionContext:
    db: Session
    tenant: Tenant
    table_id: UUID
    session: "DiningSession"  # noqa: F821


def _abandon_expired(db: Session, session) -> None:
    """Cierra la sesión y abandona su carrito abierto (sesión expirada). Sin
    impacto de inventario: el descuento solo ocurre al consolidar (Fase 4)."""
    from app.models.cart import Cart

    session.status = "closed"
    if session.closed_at is None:
        session.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    for cart in db.execute(
        select(Cart).where(Cart.session_id == session.id, Cart.status == "abierto")
    ).scalars():
        cart.status = "abandonado"
    db.commit()


@contextmanager
def open_session_context(token: str) -> Iterator[SessionContext]:
    """Verifica el token de sesión, carga la fila de `DiningSession`, aplica la
    política de expiración (cerrar + abandonar carrito) y, si está viva, desliza
    `expires_at` (ventana de 4h). Lanza 401 si el token o la sesión no son
    válidos/expiraron."""
    from app.models.dining_session import DiningSession

    try:
        claims = verify_session_token(token)
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión expirada. Vuelve a escanear el QR.",
        )
    except SessionInvalidError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de sesión inválido"
        )

    tenant = resolve_tenant_by_id(claims.tenant_id)
    with with_db(tenant.schema) as db:
        session = db.get(DiningSession, claims.session_id)
        if session is None or session.status != "open":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión no activa"
            )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if session.expires_at is not None and session.expires_at <= now:
            _abandon_expired(db, session)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión expirada por inactividad. Vuelve a escanear el QR.",
            )

        # Refresco deslizante: cada actividad corre la ventana 4h.
        session.expires_at = now + timedelta(minutes=settings.SESSION_TTL_MINUTES)
        db.commit()

        yield SessionContext(
            db=db, tenant=tenant, table_id=claims.table_id, session=session
        )


def get_session_context(
    x_session_token: str = Header(..., alias="x-session-token"),
) -> Iterator[SessionContext]:
    """Dependencia FastAPI para operaciones de carrito: autentica al comensal por
    el token de sesión y entrega db tenant-scoped + sesión (deslizada)."""
    with open_session_context(x_session_token) as ctx:
        yield ctx
