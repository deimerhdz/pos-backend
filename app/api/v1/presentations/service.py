"""Catálogo de presentaciones (spec 040, Incremento A).

Una presentación es un concepto de catálogo compartido del tenant al que
variantes de productos distintos pueden apuntar. El alcance de una regla de
promoción `qty_price_presentation` se resuelve SIEMPRE por esta referencia,
nunca por `ProductVariant.name` (FR-007).
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models.presentation import Presentation
from app.models.product_variant import ProductVariant
from app.models.promotion import Promotion, PromotionPresentationRule


def list_query(active: bool | None = None, search: str | None = None) -> Select:
    stmt = select(Presentation).order_by(Presentation.name)
    if active is not None:
        stmt = stmt.where(Presentation.active.is_(active))
    if search:
        stmt = stmt.where(Presentation.name.ilike(f"%{search.strip()}%"))
    return stmt


def applicable_variant_counts(db: Session, presentation_ids: list[UUID]) -> dict[UUID, int]:
    """`{presentation_id: nº de variantes ACTIVAS que la referencian}` en una
    sola consulta, para el panel "Productos Aplicables" (FR-005)."""
    if not presentation_ids:
        return {}
    rows = db.execute(
        select(ProductVariant.presentation_id, func.count(ProductVariant.id))
        .where(
            ProductVariant.presentation_id.in_(presentation_ids),
            ProductVariant.active.is_(True),
        )
        .group_by(ProductVariant.presentation_id)
    ).all()
    return {pid: count for pid, count in rows}


def applicable_variants(db: Session, presentation_id: UUID) -> list[ProductVariant]:
    """Variantes ACTIVAS que referencian la presentación — el alcance real de
    cualquier regla sobre ella (FR-007). Se resuelve por `presentation_id`."""
    return list(
        db.execute(
            select(ProductVariant).where(
                ProductVariant.presentation_id == presentation_id,
                ProductVariant.active.is_(True),
            )
        ).scalars()
    )


def active_promotions_using(db: Session, presentation_id: UUID) -> list[Promotion]:
    """Promociones `active` con una regla que referencia esta presentación
    (FR-020 / CL-2: bloquean su baja)."""
    return list(
        db.execute(
            select(Promotion)
            .join(PromotionPresentationRule, PromotionPresentationRule.promotion_id == Promotion.id)
            .where(
                PromotionPresentationRule.presentation_id == presentation_id,
                Promotion.status == "active",
            )
            .distinct()
        ).scalars()
    )


def _guard_in_use(db: Session, presentation: Presentation) -> None:
    """FR-020: no se puede eliminar ni desactivar una presentación referenciada
    por una regla de una promoción `active`. 409 con la lista de promociones."""
    promos = active_promotions_using(db, presentation.id)
    if promos:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": "La presentación está en uso por promociones activas",
                "promotions": [{"id": str(p.id), "name": p.name} for p in promos],
            },
        )


def create(db: Session, name: str) -> Presentation:
    presentation = Presentation(name=name)
    db.add(presentation)
    db.flush()
    return presentation


def update(db: Session, presentation: Presentation, data) -> Presentation:
    provided = data.model_fields_set
    if "active" in provided and data.active is False and presentation.active:
        _guard_in_use(db, presentation)
        presentation.active = False
    elif "active" in provided and data.active is True:
        presentation.active = True
    if "name" in provided and data.name is not None:
        presentation.name = data.name
    db.flush()
    return presentation


def delete(db: Session, presentation: Presentation) -> None:
    _guard_in_use(db, presentation)
    db.delete(presentation)
    db.flush()
