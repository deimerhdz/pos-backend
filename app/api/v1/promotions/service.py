"""Promociones: CRUD y motor de evaluación (`evaluate`) usado por el checkout.

`evaluate` calcula el descuento automático (RF-012) para las líneas de una venta
según las promociones activas y vigentes. MVP: tipos `percent` y `fixed` con
alcance por producto/categoría (o global) y vigencia por fecha/día/hora.
`combo` se selecciona explícitamente (`expand_combo`/`combo_discount_for_lines`,
no participa de `evaluate`). `buy_x_get_y`/`qty_price` quedan reservados (no
descuentan aún)."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.crud import get_or_404
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.promotion import Promotion, PromotionComboItem


def _valid_now(promo: Promotion, now: datetime) -> bool:
    # `starts_at`/`ends_at` son `DateTime` sin timezone; los callers suelen pasar
    # `datetime.now(timezone.utc)` (con timezone), lo que rompería la comparación.
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    if not promo.active:
        return False
    if promo.starts_at is not None and now < promo.starts_at:
        return False
    # `ends_at` llega como fecha sin hora desde el selector de "Hasta" (medianoche
    # de ese día): comparar por instante excluiría casi todo el último día. Se
    # compara por fecha para que "Hasta 04/08" cubra el 04/08 completo.
    if promo.ends_at is not None and now.date() > promo.ends_at.date():
        return False
    if promo.days_of_week:
        allowed = {d.strip() for d in promo.days_of_week.split(",") if d.strip() != ""}
        if str(now.weekday()) not in allowed:  # 0=lunes..6=domingo
            return False
    if promo.days_of_month:
        allowed_days = {d.strip() for d in promo.days_of_month.split(",") if d.strip() != ""}
        if str(now.day) not in allowed_days:
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


# --------------------------- Combos (selección explícita) ---------------------------

@dataclass
class ComboComponent:
    product_variant_id: UUID
    description: str
    quantity: int
    unit_price: Decimal


def get_active_combo(db: Session, combo_id: UUID, now: datetime) -> Promotion:
    promo = get_or_404(db, Promotion, combo_id, "Combo no encontrado")
    if promo.type != "combo":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "La promoción no es un combo")
    if not _valid_now(promo, now):
        raise HTTPException(status.HTTP_409_CONFLICT, "El combo no está vigente en este momento")
    return promo


def expand_combo(db: Session, combo_id: UUID, quantity: int, now: datetime) -> list[ComboComponent]:
    """Resuelve la selección explícita de un combo en sus componentes reales,
    a precio normal (el ahorro se aplica luego como descuento, no reescribiendo
    el unit_price de cada línea)."""
    promo = get_active_combo(db, combo_id, now)
    items = db.execute(
        select(PromotionComboItem).where(PromotionComboItem.promotion_id == promo.id)
    ).scalars().all()
    if not items:
        raise HTTPException(status.HTTP_409_CONFLICT, "El combo no tiene componentes configurados")

    components: list[ComboComponent] = []
    for it in items:
        variant = get_or_404(db, ProductVariant, it.product_variant_id, "Variante del combo no encontrada")
        if not variant.active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva en el combo: {variant.id}")
        product = db.get(Product, variant.product_id)
        description = f"{product.name} - {variant.name}" if product else variant.name
        components.append(ComboComponent(
            product_variant_id=variant.id,
            description=description,
            quantity=it.quantity * quantity,
            unit_price=Decimal(variant.price),
        ))
    return components


def combo_discount_for_lines(db: Session, lines: list, now: datetime) -> Decimal:
    """`lines`: objetos con `.combo_id`, `.product_variant_id`, `.quantity`,
    `.unit_price` (p.ej. `SaleLine`). Agrupa por combo_id y descuenta, por cada
    grupo, solo las unidades que forman combos completos (mínimo de
    cantidad_presente // cantidad_requerida entre los componentes) — así una
    anulación parcial deja el remanente a precio normal en vez de romper el
    cálculo. Si el combo ya no existe o no está vigente, ese grupo no descuenta."""
    by_combo: dict[UUID, list] = {}
    for line in lines:
        combo_id = getattr(line, "combo_id", None)
        if combo_id is not None:
            by_combo.setdefault(combo_id, []).append(line)

    total_discount = Decimal(0)
    for combo_id, combo_lines in by_combo.items():
        promo = db.get(Promotion, combo_id)
        if promo is None or not _valid_now(promo, now):
            continue

        recipe = db.execute(
            select(PromotionComboItem).where(PromotionComboItem.promotion_id == combo_id)
        ).scalars().all()
        if not recipe:
            continue

        qty_by_variant: dict[UUID, int] = {}
        price_by_variant: dict[UUID, Decimal] = {}
        for line in combo_lines:
            qty_by_variant[line.product_variant_id] = (
                qty_by_variant.get(line.product_variant_id, 0) + line.quantity
            )
            price_by_variant.setdefault(line.product_variant_id, Decimal(line.unit_price))

        bundle_units = min(
            qty_by_variant.get(item.product_variant_id, 0) // item.quantity
            for item in recipe
        )
        if bundle_units <= 0:
            continue

        covered_normal_total = sum(
            (price_by_variant.get(item.product_variant_id, Decimal(0)) * item.quantity * bundle_units
             for item in recipe),
            Decimal(0),
        )
        bundle_price_total = Decimal(promo.value) * bundle_units
        total_discount += max(Decimal(0), covered_normal_total - bundle_price_total)

    return total_discount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------- CRUD ---------------------------

def create(db: Session, data) -> Promotion:
    from app.models.promotion import PromotionTarget
    promo = Promotion(
        name=data.name, type=data.type.value, value=data.value, active=data.active,
        starts_at=data.starts_at, ends_at=data.ends_at, days_of_week=data.days_of_week,
        days_of_month=data.days_of_month,
        start_time=data.start_time, end_time=data.end_time, min_qty=data.min_qty,
        buy_qty=data.buy_qty, get_qty=data.get_qty,
    )
    db.add(promo)
    db.flush()
    for t in data.targets:
        db.add(PromotionTarget(promotion_id=promo.id, product_id=t.product_id, category_id=t.category_id))
    for c in data.combo_items:
        db.add(PromotionComboItem(
            promotion_id=promo.id, product_variant_id=c.product_variant_id, quantity=c.quantity,
        ))
    db.commit()
    db.refresh(promo)
    return promo


def update(db: Session, promo: Promotion, data) -> Promotion:
    # `model_fields_set` (no `is not None`): el admin debe poder limpiar un campo
    # opcional (ej. quitar días del mes) enviando `null` explícito. Con `is not
    # None` ese `null` se confundía con "campo no enviado" y nunca se aplicaba.
    provided = data.model_fields_set
    for field in ("name", "value", "active", "starts_at", "ends_at",
                  "days_of_week", "days_of_month", "start_time", "end_time", "min_qty"):
        if field in provided:
            setattr(promo, field, getattr(data, field))
    db.commit()
    db.refresh(promo)
    return promo
