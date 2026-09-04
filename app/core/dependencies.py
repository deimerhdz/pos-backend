import logging
import uuid
from datetime import timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.db import get_db, get_tenant, with_db
from app.core.models import User, Tenant
from app.core.utils import decode_token
from app.core.redis import token_in_blocklist
bearer_scheme = HTTPBearer()

logger = logging.getLogger(__name__)

from typing import Any, List

from fastapi import Depends, Request
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials

# from src.db.redis import token_in_blocklist

from .utils import decode_token
from .exceptions import (
    InvalidToken,
    RefreshTokenRequired,
    AccessTokenRequired
)



class TokenBearer(HTTPBearer):
    def __init__(self, auto_error=True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> HTTPAuthorizationCredentials | None:
        creds = await super().__call__(request)

        token_data = decode_token(creds.credentials)

        # 401 y no 403: un token caducado o inválido se resuelve re-logueándose,
        # no es una cuestión de permisos. El resto de dependencias ya usa 401.
        if token_data is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        # Un token de QR/sesión (claim `typ`) nunca es un token de usuario.
        if token_data.get("typ"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

        if await token_in_blocklist(token_data.get("jti") or ""):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail={
                                    "error": "Token has been revoked",
                                    "resolution": "Please log in again to obtain a new token"
                                })

        self.verify_token_data(token_data)

        return token_data

    def verify_token_data(self, token_data):
        raise NotImplementedError("Please Override this method in child classes")


class AccessTokenBearer(TokenBearer):
    def verify_token_data(self, token_data: dict) -> None:
        if token_data.get("refresh"):
            raise AccessTokenRequired()


class RefreshTokenBearer(TokenBearer):
    def verify_token_data(self, token_data: dict) -> None:
        if not token_data.get("refresh"):
            raise RefreshTokenRequired()


async def get_valid_token_data(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Valida un access token de usuario: firma/expiración, que no sea un refresh
    ni un token de QR/sesión, y que su jti no esté revocado.

    Se declara `async` (a diferencia de las dependencias que la consumen) para
    poder consultar el blocklist en Redis sin volver `async` a `get_current_user`,
    cuyo `db.execute()` bloqueante debe seguir corriendo en el threadpool.
    """
    token_data = decode_token(credentials.credentials)
    if not token_data or token_data.get("refresh") or token_data.get("typ"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if await token_in_blocklist(token_data.get("jti") or ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    return token_data


def _reject_if_session_revoked(user: User, token_data: dict) -> None:
    """Cierre de sesiones (spec 031, FR-009/FR-017): un JWT acuñado antes del
    último cambio de contraseña exitoso deja de aceptarse. Se evalúa **después**
    de la relectura por id + `active==True` de RN-AUTH-07/A-23, nunca en su
    lugar — el detalle es distinguible de "revocado por logout" y de
    "inactivo" (research.md Decisión 1).
    """
    if user.tokens_valid_after is None:
        return
    iat = token_data.get("iat")
    # `tokens_valid_after` es una columna DateTime naive que guarda un instante
    # UTC (convención del proyecto, app/core/timezone.py::utc_now); `.timestamp()`
    # sobre un naive lo interpretaría como hora local del servidor, no UTC.
    # Se trunca a segundo entero porque PyJWT también trunca `iat` a segundo
    # entero (`timegm(iat.utctimetuple())`): sin esto, un re-login del propio
    # Flujo B que cae en el mismo segundo de reloj que el corte (carrera real,
    # no solo de tests) generaría un `iat` truncado hacia abajo que compararía
    # como "anterior" a un `tokens_valid_after` con fracción de segundo, y el
    # backend rechazaría la sesión de origen que FR-017 exige preservar.
    cutover = int(user.tokens_valid_after.replace(tzinfo=timezone.utc).timestamp())
    if iat is not None and iat < cutover:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked due to password change",
        )


def get_current_user(
    token_data: dict = Depends(get_valid_token_data),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
    req: Request = None,
) -> User:
    """`req` (spec 074, extensión de logging operativo) lo inyecta FastAPI solo
    por su anotación `Request` — no es un parámetro que ningún llamador vía
    `Depends` tenga que pasar. Tiene default `None` a propósito: hay código que
    invoca esta función directamente, sin FastAPI de por medio (p. ej.
    `app/characterization_tests/test_auth_session_revocation.py`), y ese
    contrato no cambia. Solo se usa para el efecto colateral de abajo."""
    user = db.execute(
        select(User).where(
            User.email == token_data["user"]["email"],
            User.tenant_id == tenant.id,
            User.active == True,
        )
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    _reject_if_session_revoked(user, token_data)

    # spec 074 (research.md § 8): efecto colateral puro para
    # `OperationalLogMiddleware` — el actor ya está resuelto y validado aquí,
    # así que el middleware no repite ninguna lógica de autenticación. No
    # cambia qué devuelve esta función ni cuándo falla.
    if req is not None:
        req.state.actor_id = str(user.id)
        req.state.actor_type = "staff"

    return user


def get_shared_db():
    """Sesión sobre el schema compartido (shared). Para endpoints sin tenant
    (super admin), donde viven users/tenants/roles."""
    with with_db(None) as db:
        yield db


def get_authenticated_user(
    token_data: dict = Depends(get_valid_token_data),
    db: Session = Depends(get_shared_db),
) -> User:
    """Usuario autenticado por JWT contra el schema shared. Vale para super admin
    (tenant_id NULL) y usuarios de tenant, sin necesitar x-tenant-host."""
    uid = (token_data.get("user") or {}).get("uid")
    try:
        user_id = uuid.UUID(str(uid))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.execute(
        select(User).where(User.id == user_id, User.active == True)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    _reject_if_session_revoked(user, token_data)

    return user


def get_current_super_admin(
    request: Request,
    token_data: dict = Depends(get_valid_token_data),
    db: Session = Depends(get_shared_db),
) -> User:
    """Autentica al super admin global por JWT (sin requerir x-tenant-host)."""
    payload = token_data.get("user") or {}
    if not payload.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required",
        )

    user = db.execute(
        select(User).where(
            User.email == payload.get("email"),
            User.tenant_id.is_(None),
            User.active == True,
        )
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Super admin not found or inactive",
        )

    # Contexto para observabilidad (spec 068): el middleware de errores de
    # super-admin lo usa para etiquetar los eventos de Sentry con quién
    # originó la solicitud. Aditivo: no cambia el valor de retorno ni los
    # `raise` de arriba.
    request.state.super_admin_id = user.id

    return user


def require_tenant_admin(user: User = Depends(get_current_user)) -> User:
    """Exige que el usuario autenticado del tenant tenga rol ADMIN."""
    if not user.role or user.role.name != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required",
        )
    return user
