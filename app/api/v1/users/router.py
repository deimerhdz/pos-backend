from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.db import get_db
from app.core.dependencies import require_tenant_admin
from app.core.pagination import Page, paginate
from app.core.models import User, Role
from app.core.utils import generate_passwd_hash
from app.api.v1.users.schemas import UserCreate, UserResponse

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
