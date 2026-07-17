"""Menú público (front-end del QR). No requiere autenticación de usuario; el
tenant se resuelve por el header x-tenant-host (get_db)."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.qr_context import open_qr_context
from app.models.category import Category
from app.models.product import Product
from app.models.product_option_group import ProductOptionGroup
from app.models.option_group import OptionGroup
from app.models.dining_table import DiningTable
from app.api.v1.menu.schemas import (
    MenuCategoryResponse, MenuProductResponse, MenuVariantResponse,
    MenuOptionGroupResponse, MenuOptionResponse, MenuTableResponse,
)

router = APIRouter(prefix="/menu", tags=["menu"])


def _build_menu(db: Session) -> list[MenuCategoryResponse]:
    categories = db.execute(
        select(Category).where(Category.active.is_(True)).order_by(Category.name)
    ).scalars().all()

    products = db.execute(
        select(Product)
        .where(Product.active.is_(True))
        .options(
            selectinload(Product.variants),
            selectinload(Product.option_groups)
            .selectinload(ProductOptionGroup.option_group)
            .selectinload(OptionGroup.options),
        )
        .order_by(Product.name)
    ).scalars().all()

    by_cat: dict[UUID, list[Product]] = {}
    for p in products:
        by_cat.setdefault(p.category_id, []).append(p)

    result: list[MenuCategoryResponse] = []
    for cat in categories:
        cat_products: list[MenuProductResponse] = []
        for p in by_cat.get(cat.id, []):
            variants = [
                MenuVariantResponse(id=v.id, name=v.name, price=v.price)
                for v in p.variants if v.active
            ]
            if not variants:
                continue
            groups = []
            for link in p.option_groups:
                g = link.option_group
                groups.append(MenuOptionGroupResponse(
                    id=g.id, name=g.name,
                    min_select=link.min_select, max_select=link.max_select,
                    options=[
                        MenuOptionResponse(id=o.id, name=o.name, extra_price=o.extra_price)
                        for o in g.options if o.active
                    ],
                ))
            cat_products.append(MenuProductResponse(
                id=p.id, name=p.name, description=p.description, image_url=p.image_url,
                variants=variants, option_groups=groups,
            ))
        if cat_products:
            result.append(MenuCategoryResponse(id=cat.id, name=cat.name, products=cat_products))
    return result


@router.get("", response_model=list[MenuCategoryResponse], summary="Menú público (catálogo activo)")
def public_menu(db: Session = Depends(get_db)):
    return _build_menu(db)


@router.get("/qr/{qr_token}", summary="Resolver mesa por token QR (UUID legacy) + menú")
def menu_by_qr(qr_token: UUID, db: Session = Depends(get_db)):
    table = db.execute(
        select(DiningTable).where(DiningTable.qr_token == qr_token, DiningTable.active.is_(True))
    ).scalar_one_or_none()
    if table is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
    return {
        "table": MenuTableResponse.model_validate(table),
        "menu": _build_menu(db),
    }


@router.get("/qr-token/{token}", summary="Resolver mesa por token QR firmado + menú")
def menu_by_signed_qr(token: str):
    """Flujo público del comensal: el token firmado lleva tenant + mesa, así que
    resuelve todo sin header x-tenant-host y sin exponer el table_id plano."""
    with open_qr_context(token) as ctx:
        table = ctx.db.execute(
            select(DiningTable).where(
                DiningTable.id == ctx.table_id, DiningTable.active.is_(True)
            )
        ).scalar_one_or_none()
        if table is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
        return {
            "table": MenuTableResponse.model_validate(table),
            "menu": _build_menu(ctx.db),
        }
