"""Menú público (front-end del QR). No requiere autenticación de usuario.

`GET /menu` resuelve el tenant por el header x-tenant-host (catálogo genérico del
local); `GET /menu/qr-token/{token}` lo resuelve desde el token firmado, que es la
vía del comensal y la única que identifica una mesa."""
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db, get_tenant
from app.core.models import Tenant
from app.core.qr_context import open_qr_context
from app.core.rate_limit import enforce as rate_limit
from app.models.category import Category
from app.models.inventory_item import InventoryItem
from app.models.option import Option
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.option_group import OptionGroup
from app.models.variant_option_group import VariantOptionGroup
from app.models.dining_table import DiningTable
from app.api.v1.menu.schemas import (
    MenuCategoryResponse, MenuProductResponse, MenuVariantResponse,
    MenuOptionGroupResponse, MenuOptionResponse, MenuTableResponse,
    MenuBusinessResponse,
)

router = APIRouter(prefix="/menu", tags=["menu"])


def _option_availability(db: Session) -> dict[UUID, bool]:
    """`{option_id: hay stock}` para todas las opciones que ligan insumo, en dos
    queries constantes (nada de N+1 por producto).

    El flag es **por opción, no por (variante × opción)**: se compara el stock contra
    el peor caso del catálogo, la mayor cantidad que le pide cualquier presentación. O
    sea que "Fresa" puede salir agotada para la ensalada pequeña (60 g) porque la grande
    necesita 180 y solo quedan 100. Es el error seguro; la alternativa exacta obligaría
    a exponer la configuración de cada tamaño en el menú público. `check_availability`
    sigue siendo la red fina, con su 409 al añadir al carrito.
    """
    # Por grupo: la mayor cantidad que define un tamaño, y si alguno se queda en 0 (en
    # cuyo caso manda la cantidad propia de la opción).
    por_grupo: dict[UUID, tuple[Decimal, bool]] = {
        gid: (Decimal(mayor or 0), bool(sin_definir))
        for gid, mayor, sin_definir in db.execute(
            select(
                VariantOptionGroup.option_group_id,
                func.max(VariantOptionGroup.quantity_per_option),
                func.bool_or(VariantOptionGroup.quantity_per_option <= 0),
            ).group_by(VariantOptionGroup.option_group_id)
        ).all()
    }

    rows = db.execute(
        select(
            Option.id, Option.option_group_id, Option.item_quantity,
            InventoryItem.current_stock, InventoryItem.active,
        ).join(InventoryItem, InventoryItem.id == Option.inventory_item_id)
    ).all()

    out: dict[UUID, bool] = {}
    for option_id, group_id, item_qty, stock, item_active in rows:
        mayor, alguno_sin_definir = por_grupo.get(group_id, (Decimal(0), True))
        # La cantidad de la opción solo cuenta si algún tamaño la deja mandar: si todos
        # definen la suya, ese valor no aplica en ningún sitio e inflaría el umbral.
        need = max(mayor, Decimal(item_qty)) if alguno_sin_definir else mayor
        # Una opción ligada a un insumo que no pide cantidad en ningún sitio no
        # descuenta nada, así que no puede agotarse; pero sí desaparece si el insumo
        # se desactiva.
        out[option_id] = bool(item_active) and (need <= 0 or Decimal(stock) >= need)
    return out


def _build_menu(db: Session) -> list[MenuCategoryResponse]:
    avail = _option_availability(db)

    categories = db.execute(
        select(Category).where(Category.active.is_(True)).order_by(Category.name)
    ).scalars().all()

    products = db.execute(
        select(Product)
        .where(Product.active.is_(True), Product.available.is_(True))
        .options(
            selectinload(Product.variants)
            .selectinload(ProductVariant.option_groups)
            .selectinload(VariantOptionGroup.option_group)
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
            variants: list[MenuVariantResponse] = []
            union: dict[UUID, MenuOptionGroupResponse] = {}
            pedible = False

            for v in p.variants:
                if not v.active:
                    continue
                groups = []
                v_pedible = True
                for link in v.option_groups:
                    g = link.option_group
                    if not g.active:
                        continue
                    options = [
                        MenuOptionResponse(
                            id=o.id, name=o.name, extra_price=o.extra_price,
                            available=avail.get(o.id, True),
                        )
                        for o in g.options if o.active
                    ]
                    # Un grupo obligatorio sin ninguna opción con stock deja esta
                    # presentación imposible de pedir: mejor decirlo aquí que dejar al
                    # comensal chocar con el 409 al añadir al carrito.
                    if link.min_select > 0 and not any(o.available for o in options):
                        v_pedible = False
                    grupo = MenuOptionGroupResponse(
                        id=g.id, name=g.name,
                        min_select=link.min_select, max_select=link.max_select,
                        options=options,
                    )
                    groups.append(grupo)
                    union.setdefault(g.id, grupo)

                variants.append(MenuVariantResponse(
                    id=v.id, name=v.name, price=v.price,
                    option_groups=groups, available=v_pedible,
                ))
                pedible = pedible or v_pedible

            if not variants:
                continue

            cat_products.append(MenuProductResponse(
                id=p.id, name=p.name, description=p.description, image_url=p.image_url,
                variants=variants, option_groups=list(union.values()), available=pedible,
            ))
        if cat_products:
            result.append(MenuCategoryResponse(id=cat.id, name=cat.name, products=cat_products))
    return result


@router.get("", response_model=list[MenuCategoryResponse], summary="Menú público (catálogo activo)")
def public_menu(db: Session = Depends(get_db)):
    return _build_menu(db)


# NOTA: se eliminó `GET /menu/qr/{qr_token}` (UUID plano + header x-tenant-host).
# Resolvía el tenant desde una cabecera falsificable y exponía un identificador de
# mesa editable por el cliente; lo reemplaza `/menu/qr-token/{token}`, donde tenant
# y mesa viajan firmados dentro del token.


@router.get("/qr-token/{token}", summary="Resolver mesa por token QR firmado + menú")
async def menu_by_signed_qr(token: str, request: Request):
    """Flujo público del comensal: el token firmado lleva tenant + mesa, así que
    resuelve todo sin header x-tenant-host y sin exponer el table_id plano."""
    # Por IP antes de verificar la firma (un token basura no debe salir gratis)
    # y por mesa después, ya con el table_id de confianza.
    await rate_limit(request, "menu_qr")
    with open_qr_context(token) as ctx:
        await rate_limit(request, "menu_qr", table_id=ctx.table_id)
        table = ctx.db.execute(
            select(DiningTable).where(
                DiningTable.id == ctx.table_id, DiningTable.active.is_(True)
            )
        ).scalar_one_or_none()
        if table is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Mesa no encontrada o inactiva")
        return {
            "table": MenuTableResponse.model_validate(table),
            "business": MenuBusinessResponse.model_validate(ctx.tenant),
            "menu": _build_menu(ctx.db),
        }
