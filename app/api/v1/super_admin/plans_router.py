from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_shared_db
from app.core.crud import ensure_unique, get_or_404
from app.models.plan import Plan
from app.api.v1.super_admin.schemas import PlanCreate, PlanResponse, PlanUpdate

router = APIRouter(prefix="/plans", tags=["super-admin"])


@router.get(
    "",
    response_model=list[PlanResponse],
    summary="Listar el catálogo de planes",
    description="Devuelve todos los planes de suscripción. Solo el super admin.",
)
def list_plans(db: Session = Depends(get_shared_db)):
    return db.execute(select(Plan).order_by(Plan.name)).scalars().all()


@router.post(
    "",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un plan",
    description=(
        "Crea un plan de suscripción (FR-001). Toda característica omitida queda bloqueada "
        "(límite 0 / acceso denegado, FR-002); `null` explícito en un límite es 'ilimitado' (FR-007)."
    ),
    responses={409: {"description": "Ya existe un plan con ese nombre."}},
)
def create_plan(body: PlanCreate, db: Session = Depends(get_shared_db)):
    ensure_unique(db, Plan, Plan.name, body.name, "Ya existe un plan con ese nombre")
    plan = Plan(**body.model_dump())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.patch(
    "/{plan_id}",
    response_model=PlanResponse,
    summary="Editar un plan",
    description=(
        "Edita cualquier subconjunto de campos de un plan existente (FR-001). El cambio aplica "
        "de inmediato a todos los tenants con este plan (FR-014)."
    ),
    responses={
        404: {"description": "No existe ese plan."},
        409: {"description": "El nuevo nombre colisiona con otro plan."},
    },
)
def update_plan(plan_id: UUID, body: PlanUpdate, db: Session = Depends(get_shared_db)):
    plan = get_or_404(db, Plan, plan_id, "No existe ese plan")

    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] != plan.name:
        ensure_unique(
            db, Plan, Plan.name, updates["name"], "Ya existe un plan con ese nombre", exclude_id=plan.id,
        )
    for key, value in updates.items():
        setattr(plan, key, value)

    db.commit()
    db.refresh(plan)
    return plan
