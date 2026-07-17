"""Contrato de tokens firmados para el flujo público de mesas (QR / sesión de
comensal). Fase 0 de la especificación de gestión de mesas.

Dos tipos de token, ambos HS256 con el mismo motor que la auth (PyJWT), pero
aislados de los tokens de usuario por un claim ``typ`` obligatorio:

- **QR** (``typ="qr"``): impreso en la mesa, permanente (sin ``exp``). Codifica
  ``tenant_id`` + ``table_id`` para que el comensal resuelva tenant y mesa sin
  exponer el ``table_id`` plano en la URL ni depender del header x-tenant-host.
- **Sesión** (``typ="sess"``): emitido tras ingresar el nombre, con ``exp``
  (TTL configurable). Codifica además ``session_id`` para el carrito por comensal.

Los tokens de auth NO llevan ``typ`` (llevan ``user``/``refresh``); estos NO
llevan ``user``/``refresh``. ``verify_*`` exige esa separación en ambos sentidos
para que un token no pueda usarse fuera de su dominio aunque comparta secreto.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

from app.core.config import settings

QR_TYP = "qr"
SESSION_TYP = "sess"


class QrTokenError(Exception):
    """Token de QR inválido (firma, tipo o estructura)."""


class SessionInvalidError(Exception):
    """Token de sesión inválido por firma, tipo o estructura (no por expiración)."""


class SessionExpiredError(Exception):
    """Token de sesión con firma válida pero expirado. Se distingue de
    ``SessionInvalidError`` para que Fase 3 aplique refresco deslizante en vez
    de rechazo definitivo."""


@dataclass(frozen=True)
class QrClaims:
    tenant_id: int
    table_id: UUID


@dataclass(frozen=True)
class SessionClaims:
    tenant_id: int
    table_id: UUID
    session_id: UUID
    expires_at: datetime


def _secret() -> str:
    return settings.QR_TOKEN_SECRET or settings.JWT_SECRET


def _reject_auth_token(payload: dict) -> None:
    """Un token que trae claims de auth nunca es un token de mesa, aunque la
    firma sea válida (mismo secreto)."""
    if "user" in payload or "refresh" in payload:
        raise ValueError("auth token presented where a table token is expected")


# --------------------------------------------------------------------------- QR

def mint_qr_token(tenant_id: int, table_id: UUID) -> str:
    """Firma el token permanente del QR de una mesa. Sin ``exp``."""
    payload = {"typ": QR_TYP, "t": int(tenant_id), "tb": str(table_id)}
    return jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM)


def verify_qr_token(token: str) -> QrClaims:
    """Verifica firma + ``typ`` y devuelve los claims. Lanza ``QrTokenError``
    ante cualquier problema (firma, tipo, estructura, token de auth)."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.JWT_ALGORITHM])
        _reject_auth_token(payload)
        if payload.get("typ") != QR_TYP:
            raise ValueError("wrong token type")
        return QrClaims(tenant_id=int(payload["t"]), table_id=UUID(payload["tb"]))
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as e:
        raise QrTokenError(str(e)) from e


# ---------------------------------------------------------------------- Sesión

def mint_session_token(
    tenant_id: int,
    table_id: UUID,
    session_id: UUID,
    ttl_minutes: int | None = None,
) -> str:
    """Firma el token de sesión de un comensal con ``exp`` = ahora + TTL."""
    ttl = settings.SESSION_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    exp = datetime.now(timezone.utc) + timedelta(minutes=ttl)
    payload = {
        "typ": SESSION_TYP,
        "t": int(tenant_id),
        "tb": str(table_id),
        "s": str(session_id),
        "exp": exp,
    }
    return jwt.encode(payload, _secret(), algorithm=settings.JWT_ALGORITHM)


def verify_session_token(token: str) -> SessionClaims:
    """Verifica firma + ``typ`` + ``exp``. Lanza ``SessionExpiredError`` si la
    firma es válida pero expiró, ``SessionInvalidError`` en cualquier otro caso.

    Stateless: no consulta la fila de sesión (estado open/closed) — esa
    validación y el refresco deslizante persistido llegan en Fase 3."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise SessionExpiredError(str(e)) from e
    except jwt.PyJWTError as e:
        raise SessionInvalidError(str(e)) from e

    try:
        _reject_auth_token(payload)
        if payload.get("typ") != SESSION_TYP:
            raise ValueError("wrong token type")
        return SessionClaims(
            tenant_id=int(payload["t"]),
            table_id=UUID(payload["tb"]),
            session_id=UUID(payload["s"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise SessionInvalidError(str(e)) from e
