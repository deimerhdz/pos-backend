"""CRUD de promociones (RF-008..011). La aplicación automática en la venta
(RF-012) vive en el checkout de ventas, que llama a `service.evaluate`."""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404, ensure_unique
from app.core.dependencies import get_current_user, require_tenant_admin
from app.core.models import User
from app.models.promotion import Promotion
from app.api.v1.promotions import service
from app.api.v1.promotions.schemas import (
    PromotionCreate, PromotionUpdate, PromotionResponse,
)

router = APIRouter(prefix="/promotions", tags=["promotions"])


@router.get("", response_model=list[PromotionResponse], summary="Listar promociones")
def list_promotions(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.execute(
        select(Promotion).options(selectinload(Promotion.targets)).order_by(Promotion.name)
    ).scalars().all()


@router.post("", response_model=PromotionResponse, status_code=status.HTTP_201_CREATED, summary="Crear promoción")
def create_promotion(body: PromotionCreate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    ensure_unique(db, Promotion, Promotion.name, body.name, "Ya existe una promoción con ese nombre")
    return service.create(db, body)


@router.patch("/{promotion_id}", response_model=PromotionResponse, summary="Actualizar promoción")
def update_promotion(promotion_id: UUID, body: PromotionUpdate, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    return service.update(db, promo, body)


@router.delete("/{promotion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar promoción")
def delete_promotion(promotion_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_tenant_admin)):
    promo = get_or_404(db, Promotion, promotion_id, "Promoción no encontrada")
    db.delete(promo)
    db.commit()
