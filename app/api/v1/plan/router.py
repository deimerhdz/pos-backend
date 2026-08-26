from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db, get_tenant
from app.core.dependencies import get_current_user
from app.core.models import Tenant, User
from app.api.v1.plan import service
from app.api.v1.plan.schemas import PlanSummaryResponse

router = APIRouter(prefix="/plan", tags=["plan"])


@router.get(
    "",
    response_model=PlanSummaryResponse,
    summary="Consultar el plan vigente y el consumo del tenant",
    description=(
        "Nombre del plan, consumo de cada límite, estado de cada módulo, y vencimiento "
        "(Historia de Usuario 6, FR-013). Accesible a cualquier usuario autenticado del "
        "tenant, no solo ADMIN — research.md Decisión 7: el guard de navegación del "
        "frontend lo necesita para cualquier rol."
    ),
)
def get_plan_summary(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(get_current_user),
):
    return service.build_plan_summary(db, tenant)
