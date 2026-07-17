"""Contexto público del comensal a partir de un token de QR firmado.

Reemplaza la resolución por header x-tenant-host en el flujo público: el token
firmado lleva tenant + mesa, así que resolvemos el schema del tenant y abrimos
una sesión ya scopeada, sin exponer el table_id plano ni confiar en headers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import resolve_tenant_by_id, with_db
from app.core.models import Tenant
from app.core.qr_token import QrTokenError, verify_qr_token


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
