"""Reportes: consultas de agregación de solo lectura sobre las ventas cerradas
(`sales.status = 'paid'`), inventario y rentabilidad. Sin tablas propias.

La ventana temporal filtra por `sales.sold_at`: [date_from 00:00, date_to+1d)."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.sale import Sale, SaleItem
from app.models.product_variant import ProductVariant
from app.models.product import Product
from app.models.category import Category
from app.models.recipe_item import RecipeItem
from app.models.inventory_item import InventoryItem


def _paid_sales_filter(date_from: date | None, date_to: date | None):
    conds = [Sale.status == "paid"]
    if date_from is not None:
        conds.append(Sale.sold_at >= date_from)
    if date_to is not None:
        conds.append(Sale.sold_at < date_to + timedelta(days=1))
    return conds


def sales_report(db: Session, date_from, date_to) -> dict:
    conds = _paid_sales_filter(date_from, date_to)
    total, count = db.execute(
        select(func.coalesce(func.sum(Sale.total), 0), func.count(Sale.id)).where(*conds)
    ).one()
    total = Decimal(total)
    by_day = [
        {"day": d, "total": Decimal(t), "count": c}
        for d, t, c in db.execute(
            select(func.date(Sale.sold_at), func.sum(Sale.total), func.count(Sale.id))
            .where(*conds).group_by(func.date(Sale.sold_at)).order_by(func.date(Sale.sold_at))
        ).all()
    ]
    avg = (total / count) if count else Decimal(0)
    return {
        "date_from": date_from, "date_to": date_to,
        "total_sales": total, "ticket_count": count,
        "avg_ticket": avg.quantize(Decimal("0.01")), "by_day": by_day,
    }


def products_report(db: Session, date_from, date_to, limit: int | None) -> list[dict]:
    conds = _paid_sales_filter(date_from, date_to)
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


def categories_report(db: Session, date_from, date_to) -> list[dict]:
    conds = _paid_sales_filter(date_from, date_to)
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


def cashiers_report(db: Session, date_from, date_to) -> list[dict]:
    conds = _paid_sales_filter(date_from, date_to)
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


def _variant_unit_cost_map(db: Session) -> dict:
    """Costo unitario (COGS) de cada variante = Σ recipe_item.quantity * insumo.unit_cost."""
    rows = db.execute(
        select(
            RecipeItem.product_variant_id,
            func.coalesce(func.sum(RecipeItem.quantity * InventoryItem.unit_cost), 0),
        )
        .join(InventoryItem, InventoryItem.id == RecipeItem.inventory_item_id)
        .group_by(RecipeItem.product_variant_id)
    ).all()
    return {vid: Decimal(c) for vid, c in rows}


def profitability_report(db: Session, date_from, date_to) -> dict:
    conds = _paid_sales_filter(date_from, date_to)
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
