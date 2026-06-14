import logging

from fastapi import APIRouter, Depends

from app.core.db import tenant_create
from app.core.dependencies import get_current_super_admin
from app.core.mail import welcome_email_body
from app.core.utils import generate_random_password
from app.celery_task import send_email_task
from .schema import TenantCreateWithUser

router = APIRouter(prefix="/admin", tags=["admin"])

logger = logging.getLogger(__name__)


@router.post("/tenants", dependencies=[Depends(get_current_super_admin)])
def create_tenant(body: TenantCreateWithUser):
    password = generate_random_password()

    tenant_create(
        name=body.tenant_name,
        schema=body.schema_name,
        host=body.host,
        admin_name=body.name,
        admin_email=body.email,
        admin_password=password,
    )

    login_url = f"https://{body.host}"
    try:
        send_email_task.delay(
            recipients=[body.email],
            subject=f"Bienvenido a {body.tenant_name}",
            body=welcome_email_body(
                tenant_name=body.tenant_name,
                login_url=login_url,
                email=body.email,
                password=password,
            ),
        )
    except Exception:
        # El tenant ya fue creado y commiteado; no romper la respuesta si la
        # mensajería (Redis/worker) no está disponible.
        logger.warning(
            "No se pudo encolar el correo de bienvenida para el tenant '%s'",
            body.tenant_name,
            exc_info=True,
        )

    return {"status": "ok", "tenant": body.tenant_name}
