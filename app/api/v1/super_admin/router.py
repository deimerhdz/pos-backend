from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.dependencies import get_shared_db, get_current_super_admin
from app.core.pagination import Page, paginate
from app.core.models import User, Tenant
from app.api.v1.users.schemas import UserResponse
from app.api.v1.super_admin.schemas import TenantResponse

router = APIRouter(
    prefix="/super-admin",
    tags=["super-admin"],
    dependencies=[Depends(get_current_super_admin)],
)


@router.get(
    "/users",
    response_model=Page[UserResponse],
    summary="Listar todos los usuarios",
    description="Devuelve, de forma paginada, todos los usuarios del sistema. Permite filtrar por tenant. Solo el super admin.",
    response_description="Página de usuarios.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "Se requiere acceso de super admin."},
    },
)
def list_all_users(
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    tenant_id: int | None = Query(None, description="Filtra los usuarios por tenant."),
    db: Session = Depends(get_shared_db),
):
    stmt = (
        select(User)
        .options(selectinload(User.role), selectinload(User.tenant))
        .order_by(User.created_at.desc())
    )
    if tenant_id is not None:
        stmt = stmt.where(User.tenant_id == tenant_id)
    return paginate(db, stmt, page, size)


@router.get(
    "/tenants",
    response_model=Page[TenantResponse],
    summary="Listar todos los tenants",
    description="Devuelve, de forma paginada, todos los tenants registrados. Solo el super admin.",
    response_description="Página de tenants.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "Se requiere acceso de super admin."},
    },
)
def list_all_tenants(
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    db: Session = Depends(get_shared_db),
):
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    return paginate(db, stmt, page, size)
