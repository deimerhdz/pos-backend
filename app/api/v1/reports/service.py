"""Reportes: consultas de agregación de solo lectura sobre las ventas cerradas
(`sales.status = 'paid'`), inventario y rentabilidad. Sin tablas propias.

La ventana temporal filtra por `sales.sold_at`: [medianoche local de date_from,
medianoche local del día siguiente a date_to) — zona horaria del tenant, no
UTC (spec 030, FR-004)."""
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, select, func
from sqlalchemy.orm import Session

from app.core.timezone import local_day_bounds_utc, resolve_timezone
from app.models.sale import Sale, SaleItem
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.category import Category
from app.models.option import Option
from app.models.recipe_item import RecipeItem
from app.models.variant_option_group import VariantOptionGroup
from app.models.inventory_item import InventoryItem


def _paid_sales_filter(tenant, date_from: date | None, date_to: date | None):
    conds = [Sale.status == "paid"]
    tz = resolve_timezone(tenant)
    if date_from is not None:
        start, _ = local_day_bounds_utc(date_from, tz)
        conds.append(Sale.sold_at >= start)
    if date_to is not None:
        _, end = local_day_bounds_utc(date_to, tz)
        conds.append(Sale.sold_at < end)
    return conds


def sales_report(db: Session, tenant, date_from, date_to, group_by: str = "day") -> dict:
    conds = _paid_sales_filter(tenant, date_from, date_to)
    total, count = db.execute(
        select(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).where(*conds)
    ).one()
    total = Decimal(total)
    # Un rango anual por día son 365 puntos, que ninguna gráfica dibuja. Con
    # `month` el bucket es el primer día del mes: el consumidor sigue recibiendo
    # fechas y no tiene que aprender un formato nuevo.
    #
    # `Sale.sold_at` es UTC sin marca de zona: el doble `AT TIME ZONE`
    # reinterpreta el valor como un instante en la zona del tenant antes de
    # truncar, para que el bucket agrupe por día/mes de negocio, no UTC.
    tz_name = resolve_timezone(tenant).key
    local_sold_at = func.timezone(tz_name, func.timezone("UTC", Sale.sold_at))
    bucket = (
        func.cast(func.date_trunc("month", local_sold_at), Date)
        if group_by == "month"
        else func.date(local_sold_at)
    )
    by_day = [
        {"day": d, "total": Decimal(t), "count": c}
        for d, t, c in db.execute(
            select(bucket, func.sum(Sale.total), func.count(Sale.id))
            .where(*conds).group_by(bucket).order_by(bucket)
        ).all()
    ]
    avg = (total / count) if count else Decimal(0)
    return {
        "date_from": date_from, "date_to": date_to,
        "total_sales": total, "ticket_count": count,
        "avg_ticket": avg.quantize(Decimal("0.01")), "by_day": by_day,
    }


def products_report(db: Session, tenant, date_from, date_to, limit: int | None) -> list[dict]:
    conds = _paid_sales_filter(tenant, date_from, date_to)
    stmt = (
        select(
            SaleItem.product_variant_id,
            func.max(SaleItem.description),
            func.sum(SaleItem.quantity),
            func.sum(SaleItem.line_total),
        )
        .select_from(SaleItem).join(Sale, Sale.id == SaleItem.sale_id).where(*conds)
        .group_by(SaleItem.product_variant_id)
        .order_by(func.sum(SaleItem.line_total).desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return [
        {"product_variant_id": vid, "description": desc, "units": int(units), "revenue": Decimal(rev)}
        for vid, desc, units, rev in db.execute(stmt).all()
    ]


def categories_report(db: Session, tenant, date_from, date_to) -> list[dict]:
    conds = _paid_sales_filter(tenant, date_from, date_to)
    rows = db.execute(
        select(
            Category.id, Category.name,
            func.sum(SaleItem.quantity), func.sum(SaleItem.line_total),
        )
        .select_from(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .join(ProductVariant, ProductVariant.id == SaleItem.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(*conds)
        .group_by(Category.id, Category.name)
        .order_by(func.sum(SaleItem.line_total).desc())
    ).all()
    return [
        {"category_id": cid, "category_name": cname, "units": int(u), "revenue": Decimal(r)}
        for cid, cname, u, r in rows
    ]


def cashiers_report(db: Session, tenant, date_from, date_to) -> list[dict]:
    conds = _paid_sales_filter(tenant, date_from, date_to)
    rows = db.execute(
        select(Sale.user_id, func.max(Sale.user_name), func.count(Sale.id), func.sum(Sale.total))
        .where(*conds).group_by(Sale.user_id).order_by(func.sum(Sale.total).desc())
    ).all()
    return [
        {"user_id": uid, "user_name": uname, "ticket_count": c, "total": Decimal(t)}
        for uid, uname, c, t in rows
    ]


def inventory_report(db: Session) -> list[dict]:
    rows = db.execute(
        select(InventoryItem).where(InventoryItem.active.is_(True)).order_by(InventoryItem.name)
    ).scalars().all()
    out = []
    for it in rows:
        stock = Decimal(it.current_stock)
        cost = Decimal(it.unit_cost)
        out.append({
            "inventory_item_id": it.id, "name": it.name,
            "current_stock": stock, "min_stock": Decimal(it.min_stock),
            "unit_cost": cost, "stock_value": (stock * cost).quantize(Decimal("0.01")),
            "below_min": stock <= Decimal(it.min_stock),
        })
    return out


def _option_group_cost_basis(db: Session) -> dict:
    """Por grupo: `(costo unitario medio del insumo, cantidad propia media de la opción)`.

    Lo que consume un grupo no tiene insumo fijo: depende de lo que elija el cliente.
    Para el COGS se estima con el promedio de las opciones activas que sí ligan insumo
    — si los sabores cuestan parecido el error es despreciable, y es mucho mejor que
    ignorarlos, que dejaría el margen inflado.

    La cantidad propia se necesita porque es la que aplica cuando el tamaño no define
    ninguna (la misma regla que usa `plan_line_consumption`)."""
    rows = db.execute(
        select(
            Option.option_group_id,
            func.avg(InventoryItem.unit_cost),
            func.avg(Option.item_quantity),
        )
        .join(InventoryItem, InventoryItem.id == Option.inventory_item_id)
        .where(Option.active.is_(True))
        .group_by(Option.option_group_id)
    ).all()
    return {
        gid: (Decimal(cost), Decimal(qty or 0))
        for gid, cost, qty in rows
        if cost is not None
    }


def _variant_unit_cost_map(db: Session) -> dict:
    """Costo unitario (COGS) de cada variante: insumos fijos de la receta + lo que
    consumen los grupos que ofrece, valorados al promedio de su grupo (ver arriba).

    El aporte del grupo se cuenta `min_select` veces: es lo que el cliente elegirá como
    mínimo, y para los grupos obligatorios (`min = max`, el caso de los sabores) es
    exacto. Un grupo opcional (`min = 0`) no suma nada al costo estimado.

    La cantidad sale de la misma regla que el descuento real: manda la del tamaño y, si
    no la define, la de la opción. Contar solo las que define el tamaño dejaba fuera
    todo el helado mientras el catálogo siguiera con las cantidades en las opciones, e
    inflaba el margen."""
    basis = _option_group_cost_basis(db)

    out: dict = {}

    fijos = db.execute(
        select(RecipeItem.product_variant_id, RecipeItem.quantity, InventoryItem.unit_cost)
        .join(InventoryItem, InventoryItem.id == RecipeItem.inventory_item_id)
    ).all()
    for variant_id, quantity, unit_cost in fijos:
        out[variant_id] = out.get(variant_id, Decimal(0)) + Decimal(quantity) * Decimal(unit_cost)

    grupos = db.execute(
        select(
            VariantOptionGroup.product_variant_id,
            VariantOptionGroup.option_group_id,
            VariantOptionGroup.quantity_per_option,
            VariantOptionGroup.min_select,
        ).where(VariantOptionGroup.min_select > 0)
    ).all()
    for variant_id, group_id, qty_per_option, min_select in grupos:
        datos = basis.get(group_id)
        if datos is None:
            continue
        cost, qty_opcion = datos
        por_opcion = Decimal(qty_per_option) if qty_per_option > 0 else qty_opcion
        if por_opcion <= 0:
            continue
        aporte = por_opcion * Decimal(min_select) * cost
        out[variant_id] = out.get(variant_id, Decimal(0)) + aporte

    return out


def profitability_report(db: Session, tenant, date_from, date_to) -> dict:
    conds = _paid_sales_filter(tenant, date_from, date_to)
    cost_map = _variant_unit_cost_map(db)
    # Filas de venta con su categoría.
    rows = db.execute(
        select(
            Category.id, Category.name,
            SaleItem.product_variant_id, SaleItem.quantity, SaleItem.line_total,
        )
        .select_from(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .join(ProductVariant, ProductVariant.id == SaleItem.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(*conds)
    ).all()

    by_cat: dict = {}
    tot_rev = Decimal(0)
    tot_cogs = Decimal(0)
    for cid, cname, vid, qty, line_total in rows:
        rev = Decimal(line_total)
        cogs = cost_map.get(vid, Decimal(0)) * Decimal(qty)
        tot_rev += rev
        tot_cogs += cogs
        b = by_cat.setdefault(cid, {"category_id": cid, "category_name": cname,
                                    "revenue": Decimal(0), "cogs": Decimal(0)})
        b["revenue"] += rev
        b["cogs"] += cogs

    by_category = []
    for b in by_cat.values():
        b["margin"] = b["revenue"] - b["cogs"]
        by_category.append(b)
    by_category.sort(key=lambda x: x["margin"], reverse=True)

    return {
        "date_from": date_from, "date_to": date_to,
        "revenue": tot_rev, "cogs": tot_cogs, "margin": tot_rev - tot_cogs,
        "by_category": by_category,
    }
