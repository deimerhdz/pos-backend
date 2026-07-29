import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status,Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.orm import Session
from app.core.db import with_db
from app.core.models import User, Tenant
from app.core.utils import verify_password, create_access_token, generate_passwd_hash
from app.core.dependencies import RefreshTokenBearer,AccessTokenBearer, get_authenticated_user
from app.core.dependencies import get_shared_db
from app.api.v1.auth.schemas import LoginRequest, ChangePasswordRequest
from app.core.redis import add_jti_to_blocklist
auth_router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)


@auth_router.post("/login")
async def login(body: LoginRequest, req: Request):
    host_header = req.headers.get("x-tenant-host")
    host = host_header.split(":", 1)[0] if host_header else None
    logger.info(f"Intentando login para email: {body.email} (host: {host})")

    try:
        with with_db(None) as db:
            # Resolución opcional de tenant por Host. Si no llega el header o el host no
            # corresponde a ningún tenant (p. ej. login de super admin global), tenant=None.
            tenant = (
                db.query(Tenant).filter(Tenant.host == host).one_or_none()
                if host else None
            )
            logger.info(f"Tenant resuelto: {tenant}")
            logger.info(f"Tenant resuelto: {tenant.name if tenant else 'None'}")
            stmt = select(User).options(joinedload(User.role)).where(User.email == body.email)
            if tenant is not None:
                stmt = stmt.where(User.tenant_id == tenant.id)   # usuario de tenant
            else:
                stmt = stmt.where(User.tenant_id.is_(None))      # super admin global

            user = db.execute(stmt).scalar_one_or_none()

            # Validaciones dentro de la sesión para evitar objetos detached.
            logger.info(f"Usuario encontrado: {user.email if user else 'None'}")
            if not user or not verify_password(body.password, user.password_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
                )

            if not user.active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
                )

            user_data = {
                "email": user.email,
                "uid": str(user.id),
                "tenant_id": user.tenant_id,
                "is_super_admin": user.tenant_id is None,
                "role": user.role.name if user.role else None,
                "must_change_password": user.must_change_password,
            }
    except OperationalError as e:
        logger.error(f"Error de conexión a la base de datos: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection error"
        )
    except SQLAlchemyError as e:
        logger.error(f"Error en la consulta: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database query error"
        )

    access_token = create_access_token(user_data)
    refresh_token = create_access_token(user_data, refresh=True)

    return JSONResponse(
        content={
            "message": "Login successful",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user_data,
        }
    )


@auth_router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_shared_db),
):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    user.password_hash = generate_passwd_hash(body.new_password)
    user.must_change_password = False
    db.commit()

    return JSONResponse(content={"message": "Password changed successfully"})


@auth_router.get("/refresh-token")
async def get_new_access_token(
    token_details: dict = Depends(RefreshTokenBearer()),
    db: Session = Depends(get_shared_db),
):
    # La expiración ya la validó PyJWT dentro de `decode_token`; si el token
    # estuviera vencido, RefreshTokenBearer habría respondido 401 antes de llegar aquí.
    uid = (token_details.get("user") or {}).get("uid")
    try:
        user_id = uuid.UUID(str(uid))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    # Se relee el usuario en vez de reciclar los claims del refresh: si no,
    # una cuenta desactivada o con el rol cambiado seguiría emitiendo access
    # tokens válidos con datos obsoletos durante toda la vida del refresh.
    user = db.execute(
        select(User)
        .options(joinedload(User.role))
        .where(User.id == user_id, User.active == True)
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    user_data = {
        "email": user.email,
        "uid": str(user.id),
        "tenant_id": user.tenant_id,
        "is_super_admin": user.tenant_id is None,
        "role": user.role.name if user.role else None,
        "must_change_password": user.must_change_password,
    }

    return JSONResponse(content={"access_token": create_access_token(user_data)})

@auth_router.get("/logout")
async def revoke_token(token_details: dict = Depends(AccessTokenBearer())):
    jti = token_details["jti"]

    await add_jti_to_blocklist(jti, token_details.get("exp"))

    return JSONResponse(
        content={"message": "Logged Out Successfully"}, status_code=status.HTTP_200_OK
    )
