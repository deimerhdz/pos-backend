"""CRUD de promociones (RF-008..011). La aplicación automática en la venta
(RF-012) vive en el checkout de ventas, que llama a `service.evaluate`."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.core.audit import record_audit
from app.core.pagination import Page, paginate
from app.models.promotion import Promotion
from app.api.v1.promotions import service
from app.api.v1.promotions.schemas import (
    PromotionCreate, PromotionUpdate, PromotionResponse,
)

router = APIRouter(prefix="/promotions", tags=["promotions"])


@router.get("", response_model=Page[PromotionResponse], summary="Listar promociones")
def list_promotions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active: bool | None = Query(None, description="Filtra por estado activo/inactivo."),
    search: str | None = Query(None, description="Búsqueda por nombre."),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return paginate(db, service.list_query(search=search, active=active), page, size)


@router.post("", response_model=PromotionResponse, status_code=status.HTTP_201_CREATED, summary="Crear promoción")
def create_promotion(body: PromotionCreate, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    ensure_unique(db, Promotion, Promotion.name, body.name, "Ya existe una promoción con ese nombre")
    promo = service.create(db, body)
    record_audit(db, action="create", entity="promotion", entity_id=promo.id,
                 user=user, payload={"name": promo.name, "type": promo.type})
    db.commit()
    return promo


@router.patch("/{promotion_id}", response_model=PromotionResponse, summary="Actualizar promoción")
def update_promotion(promotion_id: UUID, body: PromotionUpdate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    return service.update(db, promo, body)


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar promoción")
def delete_promotion(promotion_id: UUID, db: Session = Depends(get_db), user: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    record_audit(db, action="delete", entity="promotion", entity_id=promo.id,
                 user=user, payload={"name": promo.name})
    db.delete(promo)
    db.commit()
