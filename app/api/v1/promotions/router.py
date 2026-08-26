"""CRUD de promociones. La aplicación automática en la venta vive en el checkout,
que llama a `service.evaluate` / `service.evaluate_detailed`."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.core.audit import record_audit
from app.core.pagination import Page, paginate
from app.core.plan_limits import require_module_access
from app.models.promotion import Promotion
from app.api.v1.promotions import service
from app.api.v1.promotions.schemas import (
    PromotionCreate, PromotionUpdate, PromotionShapeUpdate, PromotionStatusUpdate,
    PromotionDuplicate, PromotionResponse, PromotionWithOverlaps,
)

router = APIRouter(
    prefix="/promotions", tags=["promotions"],
    dependencies=[Depends(require_module_access("promociones"))],
)


def _with_overlaps(db: Session, promo: Promotion) -> dict:
    """La respuesta incluye con qué promociones compite. Es una **advertencia**:
    el RF pedía impedir el solapamiento, pero eso haría imposibles sus propios
    casos de uso ("10% en granizados" y "20% los martes" se solapan los martes).
    Quien decide es `priority`."""
    data = PromotionResponse.model_validate(promo).model_dump()
    data["overlaps"] = [
        {"id": o.id, "name": o.name, "priority": o.priority}
        for o in service.find_overlaps(db, promo)
    ]
    return data


@router.get("", response_model=Page[PromotionResponse], summary="Listar promociones")
def list_promotions(
    response: Response,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(
        None, alias="status", description="draft | active | paused | finished"
    ),
    search: str | None = Query(None, description="Búsqueda por nombre."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # A-09: el POS de staff necesita una hora de referencia sincronizada con el
    # servidor (en vez del reloj del dispositivo) para previsualizar vigencia
    # de promociones; este es el endpoint que ya sondea para eso.
    response.headers["X-Server-Time"] = datetime.now(timezone.utc).isoformat()
    return paginate(db, service.list_query(search=search, status_filter=status_filter), page, size)


@router.post("", response_model=PromotionWithOverlaps,
             status_code=status.HTTP_201_CREATED, summary="Crear promoción")
def create_promotion(body: PromotionCreate, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_admin)):
    ensure_unique(db, Promotion, Promotion.name, body.name, "Ya existe una promoción con ese nombre")
    promo = service.create(db, body)
    record_audit(db, action="create", entity="promotion", entity_id=promo.id,
                 user=user, payload={"name": promo.name, "type": promo.type,
                                     "status": promo.status, "priority": promo.priority})
    db.commit()
    db.refresh(promo)
    return _with_overlaps(db, promo)


@router.patch("/{promotion_id}", response_model=PromotionWithOverlaps,
              summary="Actualizar campos escalares")
def update_promotion(promotion_id: UUID, body: PromotionUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    # El PATCH no validaba el nombre único: renombrar a uno existente reventaba
    # con IntegrityError -> 500 en vez de un 409 legible.
    if "name" in body.model_fields_set and body.name is not None:
        ensure_unique(db, Promotion, Promotion.name, body.name,
                      "Ya existe una promoción con ese nombre", exclude_id=promo.id)
    before = {f: getattr(promo, f) for f in body.model_fields_set if hasattr(promo, f)}
    promo = service.update(db, promo, body)
    # El PATCH tampoco registraba auditoría, así que el "historial de
    # modificaciones" del RF no existía para el caso más frecuente.
    record_audit(db, action="update", entity="promotion", entity_id=promo.id, user=user,
                 payload={"before": {k: str(v) for k, v in before.items()},
                          "after": {k: str(getattr(promo, k)) for k in before}})
    db.commit()
    db.refresh(promo)
    return _with_overlaps(db, promo)


@router.patch("/{promotion_id}/shape", response_model=PromotionWithOverlaps,
              summary="Cambiar tipo, alcance o componentes (solo en borrador)")
def update_promotion_shape(promotion_id: UUID, body: PromotionShapeUpdate,
                           db: Session = Depends(get_db),
                           user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    promo = service.update_shape(db, promo, body)
    record_audit(db, action="update_shape", entity="promotion", entity_id=promo.id,
                 user=user, payload={"type": promo.type})
    db.commit()
    db.refresh(promo)
    return _with_overlaps(db, promo)


@router.patch("/{promotion_id}/status", response_model=PromotionResponse,
              summary="Activar, pausar o finalizar")
def change_promotion_status(promotion_id: UUID, body: PromotionStatusUpdate,
                            db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    previous = promo.status
    promo = service.change_status(db, promo, body.status.value)
    record_audit(db, action="status", entity="promotion", entity_id=promo.id,
                 user=user, payload={"from": previous, "to": promo.status})
    db.commit()
    db.refresh(promo)
    return promo


@router.post("/{promotion_id}/duplicate", response_model=PromotionResponse,
             status_code=status.HTTP_201_CREATED, summary="Duplicar promoción")
def duplicate_promotion(promotion_id: UUID, body: PromotionDuplicate,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    ensure_unique(db, Promotion, Promotion.name, body.name, "Ya existe una promoción con ese nombre")
    copy = service.duplicate(db, promo, body.name)
    record_audit(db, action="duplicate", entity="promotion", entity_id=copy.id,
                 user=user, payload={"source_id": str(promo.id), "name": copy.name})
    db.commit()
    db.refresh(copy)
    return copy


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar promoción")
def delete_promotion(promotion_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    record_audit(db, action="delete", entity="promotion", entity_id=promo.id,
                 user=user, payload={"name": promo.name})
    db.delete(promo)
    db.commit()
