from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, get_tenant
from app.core.dependencies import require_tenant_admin
from app.core.mail import create_message, invitation_email_body, send_email
from app.core.models import Role, Tenant, User, UserInvitation
from app.core.plan_limits import enforce_plan_limit
from app.core.utils import generate_passwd_hash, generate_random_password
from app.api.v1.invitations.schemas import InvitationCreate, InvitationResponse

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _build_login_url(tenant: Tenant) -> str:
    if settings.ENVIRONMENT == "prod":
        return f"https://{tenant.host}.skeilopos.com/login"
    return f"http://{tenant.host}.localhost:4200/login"


@router.post(
    "",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invitar a una persona a unirse al tenant",
    description=(
        "Crea una invitación pendiente para el correo y rol indicados — único mecanismo "
        "para dar de alta un usuario interno del tenant (FR-001/FR-003/FR-004). No crea "
        "ningún `User`; la contraseña temporal generada nunca se expone (FR-006)."
    ),
    response_description="La invitación creada.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "No es ADMIN del tenant, o el plan venció / alcanzó su límite de usuarios."},
        404: {"description": "El rol indicado no existe."},
        409: {"description": "Ya existe un usuario o una invitación pendiente con ese correo en el tenant."},
        422: {"description": "Datos de entrada inválidos."},
        502: {"description": "No se pudo enviar el correo de invitación."},
    },
)
def create_invitation(
    body: InvitationCreate,
    tenant: Tenant = Depends(get_tenant),
    admin: User = Depends(require_tenant_admin),
    db: Session = Depends(get_db),
):
    email = body.email.strip().lower()

    # 1) Plan vencido (FR-018) o límite de "usuarios" alcanzado, contando
    # también invitaciones pendientes (research.md Decisiones 5/6).
    enforce_plan_limit(db, tenant, "usuarios")

    # 2) Correo ya usado por un User del tenant, activo o inactivo (FR-015,
    # Clarification 1) — no se libera el correo de una cuenta desactivada.
    existing_user = db.execute(
        select(User).where(User.tenant_id == tenant.id, User.email == email)
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe un usuario con ese correo en el tenant",
        )

    role = db.execute(
        select(Role).where(Role.name == body.role.value)
    ).scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{body.role.value}' not found",
        )

    password = generate_random_password()
    invitation = UserInvitation(
        tenant_id=tenant.id,
        email=email,
        role_id=role.id,
        password_hash=generate_passwd_hash(password),
    )
    db.add(invitation)

    # 3) A lo sumo una invitación 'pending' por correo+tenant (índice único
    # parcial, research.md Decisión 3) — dos ADMIN invitando casi al mismo
    # tiempo dejan una sola fila viva.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya existe una invitación pendiente para ese correo",
        )

    # 4) Envío síncrono (research.md Decisión 4): un fallo deja la invitación
    # sin persistir (FR-012) — nunca se confirma un envío que no salió.
    try:
        send_email(
            create_message(
                [email],
                f"Bienvenido a {tenant.name}",
                invitation_email_body(
                    tenant_name=tenant.name,
                    login_url=_build_login_url(tenant),
                    email=email,
                    password=password,
                ),
            )
        )
    except RuntimeError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo enviar el correo de invitación. Intenta de nuevo.",
        )

    db.commit()
    db.refresh(invitation)
    return invitation
