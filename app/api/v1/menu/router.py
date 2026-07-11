import secrets
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update, delete, or_
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.crud import get_or_404
from app.core.dependencies import get_current_user
from app.core.models import User
from app.core.pagination import Page, paginate
from app.models.tables import Table
from app.models.table_session import TableSession
from app.models.product import Product
from app.models.category import Category
from app.models.cart_item import CartItem
from app.models.cart_item_modifier import CartItemModifier
from app.models.variant import Variant
from app.models.variant_value import VariantValue
from app.models.modifier_group import ModifierGroup
from app.models.product_modifier_group import ProductModifierGroup
from app.models.order import Order
from app.api.v1.menu.dependencies import get_menu_session
from app.api.v1.menu.availability import product_is_available, variant_is_available
from app.api.v1.menu.schemas import (
    MenuSessionCreate,
    MenuSessionResponse,
    MenuCategoryResponse,
    MenuProductResponse,
    MenuProductVariantsResponse,
    CartItemCreate,
    CartItemUpdate,
    CartResponse,
)
from app.api.v1.orders.schemas import OrderCreate, OrderResponse
from app.api.v1.orders.service import OrderService

order_service = OrderService()

router = APIRouter(prefix="/menu", tags=["menu"])


@router.post(
    "/sessions",
    response_model=MenuSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Abrir una sesión de menú (escanear QR)",
    description=(
        "Flujo público: el comensal escanea el QR de una mesa y registra su nombre. "
        "Se valida que la mesa exista y esté activa, y que no se supere su capacidad de "
        "sesiones simultáneas. Devuelve el token de sesión para consultar el menú."
    ),
    response_description="La sesión de menú creada con su token.",
    responses={
        404: {"description": "No existe una mesa activa con ese código QR."},
        409: {"description": "La mesa alcanzó su capacidad de sesiones simultáneas."},
        422: {"description": "Datos de entrada inválidos."},
    },
)
def create_session(
    body: MenuSessionCreate,
    db: Session = Depends(get_db),
):
    table = db.execute(
        select(Table).where(
            Table.qr_code == body.qr_code,
            Table.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if table is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No existe una mesa activa con ese código QR",
        )

    active_sessions = db.execute(
        select(func.count())
        .select_from(TableSession)
        .where(
            TableSession.table_id == table.id,
            TableSession.active == True,  # noqa: E712
        )
    ).scalar_one()

    if active_sessions >= table.capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La mesa alcanzó su capacidad de sesiones simultáneas",
        )

    session = TableSession(
        table_id=table.id,
        customer_name=body.customer_name,
        token=secrets.token_urlsafe(32),
        active=True,
    )
    db.add(session)
    table.status = "occupied"
    db.commit()
    db.refresh(session)

    return MenuSessionResponse(
        session_token=session.token,
        customer_name=session.customer_name,
        table_id=table.id,
        table_name=table.name,
        capacity=table.capacity,
    )


@router.get(
    "/categories",
    response_model=list[MenuCategoryResponse],
    summary="Listar categorías del menú",
    description=(
        "Devuelve las categorías activas para el modo de visualización por categorías. "
        "Requiere una sesión de menú válida (header 'X-Menu-Session')."
    ),
    response_description="Lista de categorías activas.",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
    },
)
def list_menu_categories(
    db: Session = Depends(get_db),
    _: TableSession = Depends(get_menu_session),
):
    return db.execute(
        select(Category).where(Category.active == True).order_by(Category.name)  # noqa: E712
    ).scalars().all()


@router.get(
    "/products",
    response_model=Page[MenuProductResponse],
    summary="Listar productos del menú",
    description=(
        "Devuelve los productos del menú (is_menu=true y activos) de forma paginada. "
        "Sin 'category_id' muestra todos (modo 1); con 'category_id' filtra por categoría "
        "(modo 2). Requiere una sesión de menú válida (header 'X-Menu-Session')."
    ),
    response_description="Página de productos del menú.",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
    },
)
def list_menu_products(
    category_id: UUID | None = Query(
        None, description="Filtra los productos por categoría (modo de visualización 2)."
    ),
    page: int = Query(1, ge=1, description="Número de página (empieza en 1)."),
    size: int = Query(20, ge=1, le=100, description="Cantidad de elementos por página (máximo 100)."),
    db: Session = Depends(get_db),
    _: TableSession = Depends(get_menu_session),
):
    stmt = (
        select(Product)
        .where(Product.is_menu == True, Product.active == True)  # noqa: E712
        .order_by(Product.name)
    )
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    page_data = paginate(db, stmt, page, size)
    # Fase 3: solo se muestran productos con disponibilidad real (stock vigente − reservas).
    kept = []
    for p in page_data["items"]:
        p.is_available = product_is_available(db, p)
        if p.is_available:
            kept.append(p)
    page_data["items"] = kept
    return page_data


@router.get(
    "/products/{product_id}/variants",
    response_model=MenuProductVariantsResponse,
    summary="Variantes y modificadores de un producto (para armar la selección)",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
        404: {"description": "El producto no está disponible en el menú."},
    },
)
def menu_product_variants(
    product_id: UUID,
    db: Session = Depends(get_db),
    _: TableSession = Depends(get_menu_session),
):
    product = db.execute(
        select(Product).where(
            Product.id == product_id, Product.is_menu == True, Product.active == True  # noqa: E712
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El producto no está disponible en el menú")

    variants = db.execute(
        select(Variant)
        .options(selectinload(Variant.values).selectinload(VariantValue.attribute_value))
        .where(Variant.product_id == product_id, Variant.active == True)  # noqa: E712
        .order_by(Variant.sku)
    ).scalars().all()
    # Fase 3: solo variantes con disponibilidad real.
    variants = [v for v in variants if variant_is_available(db, v)]

    groups = db.execute(
        select(ModifierGroup)
        .join(ProductModifierGroup, ProductModifierGroup.group_id == ModifierGroup.id)
        .where(ProductModifierGroup.product_id == product_id, ModifierGroup.active == True)  # noqa: E712
        .options(selectinload(ModifierGroup.modifiers))
    ).scalars().all()

    return {
        "product_id": product_id,
        "type": product.type,
        "variants": [
            {"id": v.id, "sku": v.sku, "price": v.price, "values": [vv.value for vv in v.values]}
            for v in variants
        ],
        "modifier_groups": [
            {
                "id": g.id, "name": g.name, "required": g.required,
                "min_select": g.min_select, "max_select": g.max_select,
                "modifiers": [
                    {"id": m.id, "name": m.name, "price": m.price} for m in g.modifiers if m.active
                ],
            }
            for g in groups
        ],
    }


# ---------------------------------------------------------------------------
# Carrito de la mesa
# ---------------------------------------------------------------------------

def _validate_modifiers(db: Session, product_id: UUID, modifier_ids: list[UUID]) -> None:
    """Valida que los modificadores pertenezcan a grupos del producto y respeten
    las reglas de selección (required/min/max)."""
    groups = db.execute(
        select(ModifierGroup)
        .join(ProductModifierGroup, ProductModifierGroup.group_id == ModifierGroup.id)
        .where(ProductModifierGroup.product_id == product_id)
        .options(selectinload(ModifierGroup.modifiers))
    ).scalars().all()

    group_by_modifier = {m.id: g for g in groups for m in g.modifiers if m.active}
    counts: dict[UUID, int] = {}
    for mid in modifier_ids:
        g = group_by_modifier.get(mid)
        if g is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Modificador {mid} no válido para este producto.",
            )
        counts[g.id] = counts.get(g.id, 0) + 1

    for g in groups:
        c = counts.get(g.id, 0)
        if g.required and c < g.min_select:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El grupo '{g.name}' requiere al menos {g.min_select} selección(es).",
            )
        if g.max_select is not None and c > g.max_select:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"El grupo '{g.name}' permite máximo {g.max_select} selección(es).",
            )


def _build_cart_response(db: Session, session: TableSession) -> CartResponse:
    """Arma el carrito completo de la mesa (items de todos los comensales) + total."""
    items = db.execute(
        select(CartItem)
        .options(
            selectinload(CartItem.variant),
            selectinload(CartItem.product),
            selectinload(CartItem.table_session),
            selectinload(CartItem.modifiers).selectinload(CartItemModifier.modifier),
        )
        .where(CartItem.table_id == session.table_id)
        .order_by(CartItem.created_at)
    ).scalars().all()

    resp_items = []
    total = Decimal("0.00")
    for ci in items:
        variant = ci.variant
        mods = [m.modifier for m in ci.modifiers if m.modifier is not None]
        unit_price = (variant.price if variant else Decimal("0.00")) + sum(
            (m.price for m in mods), Decimal("0.00")
        )
        subtotal = unit_price * ci.quantity
        total += subtotal
        resp_items.append({
            "id": ci.id,
            "variant_id": ci.variant_id,
            "product_id": ci.product_id,
            "product_name": ci.product.name if ci.product else (variant.sku if variant else ""),
            "variant_sku": variant.sku if variant else None,
            "quantity": ci.quantity,
            "unit_price": unit_price,
            "subtotal": subtotal,
            "modifiers": [
                {"modifier_id": m.id, "name": m.name, "price": m.price} for m in mods
            ],
            "table_session_id": ci.table_session_id,
            "customer_name": ci.table_session.customer_name if ci.table_session else "",
            "is_mine": ci.table_session_id == session.id,
        })

    return CartResponse(table_id=session.table_id, items=resp_items, total=total)


@router.post(
    "/cart/items",
    response_model=CartResponse,
    summary="Agregar un producto a mi carrito",
    description=(
        "Agrega un producto de menú al carrito como la sesión actual. Si el producto ya "
        "estaba en mi carrito, suma la cantidad. Devuelve el carrito completo de la mesa."
    ),
    response_description="El carrito de la mesa actualizado.",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
        404: {"description": "El producto no está disponible en el menú."},
    },
)
def add_cart_item(
    body: CartItemCreate,
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    variant = db.execute(
        select(Variant)
        .join(Product, Product.id == Variant.product_id)
        .where(
            Variant.id == body.variant_id,
            Variant.active == True,  # noqa: E712
            Product.is_menu == True,  # noqa: E712
            Product.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()
    if variant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La variante no está disponible en el menú",
        )

    _validate_modifiers(db, variant.product_id, body.modifier_ids)

    item = CartItem(
        table_id=session.table_id,
        table_session_id=session.id,
        variant_id=variant.id,
        product_id=variant.product_id,
        quantity=body.quantity,
    )
    db.add(item)
    db.flush()

    for mid in body.modifier_ids:
        db.add(CartItemModifier(cart_item_id=item.id, modifier_id=mid))

    db.commit()
    return _build_cart_response(db, session)


@router.get(
    "/cart",
    response_model=CartResponse,
    summary="Ver el carrito de la mesa",
    description=(
        "Devuelve el carrito completo de la mesa con los items de todos los comensales, "
        "indicando cuáles agregó la sesión actual ('is_mine'). Requiere sesión de menú."
    ),
    response_description="El carrito de la mesa.",
    responses={401: {"description": "Sesión de menú inválida o cerrada."}},
)
def get_cart(
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    return _build_cart_response(db, session)


@router.patch(
    "/cart/items/{item_id}",
    response_model=CartResponse,
    summary="Actualizar la cantidad de un item de mi carrito",
    description="Cambia la cantidad de un item que agregó la sesión actual.",
    response_description="El carrito de la mesa actualizado.",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
        403: {"description": "El item no pertenece a la sesión actual."},
        404: {"description": "El item no existe."},
    },
)
def update_cart_item(
    item_id: UUID,
    body: CartItemUpdate,
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    item = get_or_404(db, CartItem, item_id, "Cart item not found")
    if item.table_session_id != session.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puedes modificar los items que agregaste",
        )
    item.quantity = body.quantity
    db.commit()
    return _build_cart_response(db, session)


@router.delete(
    "/cart/items/{item_id}",
    response_model=CartResponse,
    summary="Eliminar un item de mi carrito",
    description="Elimina un item que agregó la sesión actual.",
    response_description="El carrito de la mesa actualizado.",
    responses={
        401: {"description": "Sesión de menú inválida o cerrada."},
        403: {"description": "El item no pertenece a la sesión actual."},
        404: {"description": "El item no existe."},
    },
)
def delete_cart_item(
    item_id: UUID,
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    item = get_or_404(db, CartItem, item_id, "Cart item not found")
    if item.table_session_id != session.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puedes eliminar los items que agregaste",
        )
    db.delete(item)
    db.commit()
    return _build_cart_response(db, session)


# ---------------------------------------------------------------------------
# Órdenes desde el carrito
# ---------------------------------------------------------------------------

@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generar una orden desde el carrito",
    description=(
        "Convierte el carrito en una orden de compra. Con scope='individual' usa solo los "
        "productos de la sesión actual; con scope='table' genera una única orden con los "
        "productos de toda la mesa. Descuenta inventario de los productos que lo gestionan."
    ),
    response_description="La orden generada.",
    responses={
        400: {"description": "El carrito está vacío o no hay stock suficiente."},
        401: {"description": "Sesión de menú inválida o cerrada."},
    },
)
def create_order(
    body: OrderCreate,
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    return order_service.create_from_cart(
        db, table_id=session.table_id, scope=body.scope.value, session=session
    )


@router.get(
    "/orders",
    response_model=list[OrderResponse],
    summary="Ver las órdenes de la mesa",
    description="Devuelve las órdenes generadas para la mesa de la sesión actual.",
    response_description="Órdenes de la mesa.",
    responses={401: {"description": "Sesión de menú inválida o cerrada."}},
)
def list_my_orders(
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    return db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.table_id == session.table_id)
        .order_by(Order.created_at.desc())
    ).scalars().all()


@router.get(
    "/orders/active",
    response_model=list[OrderResponse],
    summary="Ver las órdenes de las sesiones activas de la mesa",
    description=(
        "Devuelve las órdenes de la mesa filtradas a las que pertenecen a sesiones "
        "activas, más las órdenes de toda la mesa (scope='table'). Excluye las órdenes "
        "individuales de comensales cuya sesión ya fue cerrada. Si no hay coincidencias "
        "devuelve una lista vacía. Requiere una sesión de menú válida (header "
        "'X-Menu-Session')."
    ),
    response_description="Órdenes ligadas a sesiones activas de la mesa.",
    responses={401: {"description": "Sesión de menú inválida o cerrada."}},
)
def list_active_session_orders(
    db: Session = Depends(get_db),
    session: TableSession = Depends(get_menu_session),
):
    active_session_ids = select(TableSession.id).where(
        TableSession.table_id == session.table_id,
        TableSession.active == True,  # noqa: E712
    )

    return db.execute(
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.table_id == session.table_id,
            or_(
                Order.scope == "table",
                Order.table_session_id.in_(active_session_ids),
            ),
        )
        .order_by(Order.created_at.desc())
    ).scalars().all()


@router.post(
    "/tables/{table_id}/close",
    summary="Cerrar la mesa y liberar sus sesiones",
    description=(
        "Acción del staff (requiere autenticación): cierra todas las sesiones activas de la "
        "mesa y la deja como 'available', liberando la capacidad para nuevos comensales."
    ),
    response_description="Resumen de las sesiones cerradas.",
    responses={
        401: {"description": "No autenticado o token inválido."},
        404: {"description": "La mesa no existe."},
    },
)
def close_table(
    table_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    table = get_or_404(db, Table, table_id, "Table not found")

    closed = db.execute(
        update(TableSession)
        .where(
            TableSession.table_id == table.id,
            TableSession.active == True,  # noqa: E712
        )
        .values(active=False)
    ).rowcount

    # Limpia los items de carrito no convertidos en orden (las órdenes ya creadas se conservan).
    cart_ids = select(CartItem.id).where(CartItem.table_id == table.id)
    db.execute(delete(CartItemModifier).where(CartItemModifier.cart_item_id.in_(cart_ids)))
    db.execute(delete(CartItem).where(CartItem.table_id == table.id))

    table.status = "available"
    db.commit()

    return {"status": "ok", "table_id": str(table.id), "closed_sessions": closed}
