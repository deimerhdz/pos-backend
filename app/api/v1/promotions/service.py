"""Promociones: CRUD y motor de evaluación (`evaluate`) usado por el checkout.

`evaluate` calcula el descuento automático (RF-012) para las líneas de una venta
según las promociones activas y vigentes. MVP: tipos `percent` y `fixed` con
alcance por producto/categoría (o global) y vigencia por fecha/día/hora. Los
tipos `buy_x_get_y`/`combo`/`qty_price` quedan reservados (no descuentan aún)."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.promotion import Promotion


def _valid_now(promo: Promotion, now: datetime) -> bool:
    if not promo.active:
        return False
    if promo.starts_at is not None and now < promo.starts_at:
        return False
    if promo.ends_at is not None and now > promo.ends_at:
        return False
    if promo.days_of_week:
        allowed = {d.strip() for d in promo.days_of_week.split(",") if d.strip() != ""}
        if str(now.weekday()) not in allowed:  # 0=lunes..6=domingo
            return False
    if promo.start_time is not None and now.time() < promo.start_time:
        return False
    if promo.end_time is not None and now.time() > promo.end_time:
        return False
    return True


def _matches(promo: Promotion, product_id, category_id) -> bool:
    """Sin targets = global. Con targets, aplica si algún target casa el producto
    o la categoría de la línea."""
    if not promo.targets:
        return True
    for t in promo.targets:
        if t.product_id is not None and t.product_id == product_id:
            return True
        if t.category_id is not None and t.category_id == category_id:
            return True
    return False


def _line_discount(promo: Promotion, line_total: Decimal) -> Decimal:
    if promo.type == "percent":
        return (line_total * Decimal(promo.value) / Decimal(100))
    if promo.type == "fixed":
        return min(Decimal(promo.value), line_total)
    # buy_x_get_y / combo / qty_price: fase 2, sin descuento por ahora.
    return Decimal(0)


def evaluate(db: Session, lines: list[dict], now: datetime) -> tuple[Decimal, object]:
    """`lines`: dicts con product_id, category_id, quantity, line_total.
    Devuelve (descuento_total, promotion_id_aplicada | None)."""
    promos = db.execute(
        select(Promotion).options(selectinload(Promotion.targets))
    ).scalars().all()
    valid = [p for p in promos if _valid_now(p, now)]
    if not valid:
        return Decimal(0), None

    total_discount = Decimal(0)
    applied: set = set()
    for line in lines:
        best = Decimal(0)
        best_id = None
        for p in valid:
            if line["quantity"] < p.min_qty:
                continue
            if not _matches(p, line.get("product_id"), line.get("category_id")):
                continue
            d = _line_discount(p, Decimal(line["line_total"]))
            if d > best:
                best, best_id = d, p.id
        if best > 0:
            total_discount += best
            applied.add(best_id)

    total_discount = total_discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    applied_id = next(iter(applied)) if len(applied) == 1 else None
    return total_discount, applied_id


# --------------------------- CRUD ---------------------------

def create(db: Session, data) -> Promotion:
    from app.models.promotion import PromotionTarget
    promo = Promotion(
        name=data.name, type=data.type.value, value=data.value, active=data.active,
        starts_at=data.starts_at, ends_at=data.ends_at, days_of_week=data.days_of_week,
        start_time=data.start_time, end_time=data.end_time, min_qty=data.min_qty,
        buy_qty=data.buy_qty, get_qty=data.get_qty,
    )
    db.add(promo)
    db.flush()
    for t in data.targets:
        db.add(PromotionTarget(promotion_id=promo.id, product_id=t.product_id, category_id=t.category_id))
    db.commit()
    db.refresh(promo)
    return promo


def update(db: Session, promo: Promotion, data) -> Promotion:
    for field in ("name", "value", "active", "starts_at", "ends_at",
                  "days_of_week", "start_time", "end_time", "min_qty"):
        val = getattr(data, field)
        if val is not None:
            setattr(promo, field, val)
    db.commit()
    db.refresh(promo)
    return promo
