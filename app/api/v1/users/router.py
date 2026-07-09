from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import require_tenant_admin
from app.core.pagination import Page, paginate
from app.core.models import User, Role
from app.core.utils import generate_passwd_hash
from app.api.v1.users.schemas import UserCreate, UserResponse, UserRoleUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=Page[UserResponse],
    summary="Listar usuarios del tenant",
    description="Devuelve, de forma paginada, los usuarios pertenecientes al tenant del admin autenticado.",
    response_description="Página de usuarios del tenant.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "El usuario no es administrador del tenant."},
    },
)
def list_users(
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    db: Session = Depends(get_db),
    admin: User = Depends(require_tenant_admin),
):
    stmt = (
        select(User)
        .options(selectinload(User.role), selectinload(User.tenant))
        .where(User.tenant_id == admin.tenant_id)
        .order_by(User.created_at.desc())
    )
    return paginate(db, stmt, page, size)


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un usuario en el tenant",
    description=(
        "Crea un usuario dentro del tenant del admin autenticado. El email debe ser "
        "único en el tenant y el rol debe ser ADMIN o CASHIER (nunca SUPER_ADMIN)."
    ),
    response_description="El usuario creado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "El usuario no es administrador del tenant."},
        409: {"description": "Ya existe un usuario con ese email en el tenant."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_tenant_admin),
):
    existing = db.execute(
        select(User).where(
            User.email == body.email,
            User.tenant_id == admin.tenant_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists in the tenant",
        )

    role = db.execute(
        select(Role).where(Role.name == body.role.value)
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{body.role.value}' not found",
        )

    user = User(
        name=body.name,
        email=body.email,
        password_hash=generate_passwd_hash(body.password),
        phone=body.phone,
        active=True,
        role_id=role.id,
        tenant_id=admin.tenant_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Consultar un usuario del tenant por id",
    description=(
        "Devuelve los datos de un usuario del tenant del admin autenticado. "
        "No expone la contraseña."
    ),
    response_description="El usuario solicitado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "El usuario no es administrador del tenant."},
        404: {"description": "Usuario no encontrado en el tenant."},
    },
)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(require_tenant_admin),
):
    user = db.execute(
        select(User)
        .options(selectinload(User.role), selectinload(User.tenant))
        .where(User.id == user_id, User.tenant_id == admin.tenant_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.patch(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Cambiar el rol de un usuario del tenant",
    description=(
        "Cambia únicamente el rol (ADMIN o CASHIER) de un usuario del tenant del admin "
        "autenticado. El admin no puede cambiar su propio rol."
    ),
    response_description="El usuario con el rol actualizado.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "No es admin del tenant, o intenta cambiar su propio rol."},
        404: {"description": "Usuario no encontrado en el tenant."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def update_user_role(
    user_id: UUID,
    body: UserRoleUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_tenant_admin),
):
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot change your own role",
        )

    user = db.execute(
        select(User)
        .options(selectinload(User.role), selectinload(User.tenant))
        .where(User.id == user_id, User.tenant_id == admin.tenant_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    role = db.execute(
        select(Role).where(Role.name == body.role.value)
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{body.role.value}' not found",
        )

    user.role_id = role.id
    db.commit()
    db.refresh(user)
    return user
