"""Promociones: motor de evaluación y CRUD.

Tres cambios estructurales respecto de la versión anterior:

1. **La vigencia se evalúa en hora local del tenant.** Antes `_valid_now`
   descartaba el `tzinfo` de un `now` que llegaba en UTC. En UTC-5 eso no solo
   corría la ventana horaria: corría `weekday()`, `day` y el corte de
   `ends_at`. Un "20% los martes" empezaba el lunes a las 19:00 locales, que es
   justo cuando una heladería vende.

2. **`evaluate_detailed` devuelve un desglose por línea**, no un escalar. De ahí
   salen sin cálculo adicional: mostrar al cajero qué promoción se aplicó, el
   detalle del beneficio, retirar una promoción concreta y la trazabilidad que
   hoy se pierde cuando aplican dos promociones (`Sale.promotion_id` en NULL).
   `evaluate()` conserva la firma anterior delegando aquí, para migrar los
   cuatro caminos de cobro de a uno.

3. **La prioridad resuelve el conflicto.** Antes ganaba siempre el descuento
   mayor. Ahora gana la de mayor `priority`; el descuento mayor y luego
   `created_at` son los desempates, para que el resultado sea reproducible y no
   dependa del orden que devuelva el SELECT. Sigue sin haber acumulación: una
   sola promoción por línea.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.core.config import settings
from app.core.crud import get_or_404
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.promotion import (
    Promotion, PromotionComboItem, PromotionTarget, PROMOTION_TRANSITIONS,
)

# Tipos que participan del motor automático. `combo` se selecciona
# explícitamente y va por `combo_discount_for_lines`.
AUTO_TYPES = ("percent", "fixed", "qty_price")


# --------------------------- Hora local ---------------------------

def _tz() -> ZoneInfo:
    """Zona horaria de evaluación. Hoy es una sola para la instancia
    (`TENANT_TIMEZONE`). Cuando `Tenant` tenga su columna `timezone`, este es el
    único punto que cambia."""
    return ZoneInfo(settings.TENANT_TIMEZONE)


def local_now(now: datetime | None = None) -> datetime:
    """Devuelve `now` como hora local **naive** del tenant.

    Acepta un `datetime` aware (lo convierte) o naive (lo asume ya local). Los
    callers actuales pasan `datetime.now(timezone.utc)`, así que quedan
    corregidos sin tocarlos.
    """
    if now is None:
        return datetime.now(_tz()).replace(tzinfo=None)
    if now.tzinfo is not None:
        return now.astimezone(_tz()).replace(tzinfo=None)
    return now


def _in_time_window(current: time, start: time | None, end: time | None) -> bool:
    """Ventana horaria, con soporte para cruce de medianoche.

    `22:00`-`02:00` era insatisfacible en la versión anterior: la cadena AND
    exigía hora >= 22:00 **y** hora <= 02:00 a la vez. La promoción se creaba, se
    listaba como activa y descontaba cero para siempre, sin ningún error.
    """
    if start is None and end is None:
        return True
    if start is None:
        return current <= end
    if end is None:
        return current >= start
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _valid_now(promo: Promotion, now: datetime) -> bool:
    """`now` puede venir en UTC o local; se normaliza a hora local del tenant."""
    now = local_now(now)
    if promo.status != "active":
        return False
    if promo.starts_at is not None and now < promo.starts_at:
        return False
    # `ends_at` llega como medianoche del día elegido en el selector "Hasta":
    # se compara por fecha para que "Hasta 04/08" cubra el 04/08 completo.
    if promo.ends_at is not None and now.date() > promo.ends_at.date():
        return False
    if promo.days_of_week:
        allowed = {d.strip() for d in promo.days_of_week.split(",") if d.strip()}
        if str(now.weekday()) not in allowed:  # 0=lunes..6=domingo
            return False
    if promo.days_of_month:
        allowed_days = {d.strip() for d in promo.days_of_month.split(",") if d.strip()}
        if str(now.day) not in allowed_days:
            return False
    return _in_time_window(now.time(), promo.start_time, promo.end_time)


def _matches(promo: Promotion, product_id, category_id) -> bool:
    """Sin targets = global. Con targets, aplica si alguno casa el producto o la
    categoría de la línea."""
    if not promo.targets:
        return True
    for t in promo.targets:
        if t.product_id is not None and t.product_id == product_id:
            return True
        if t.category_id is not None and t.category_id == category_id:
            return True
    return False


def _line_discount(promo: Promotion, line_total: Decimal, quantity: int,
                   unit_price: Decimal) -> Decimal:
    if promo.type == "percent":
        return line_total * Decimal(promo.value) / Decimal(100)
    if promo.type == "fixed":
        return min(Decimal(promo.value), line_total)
    if promo.type == "qty_price":
        # Solo paquetes completos; el remanente se cobra a precio normal. Misma
        # semántica de "bundle completo" que los combos, para que una anulación
        # parcial degrade suave en vez de romper el cálculo.
        packs = quantity // promo.min_qty
        if packs <= 0:
            return Decimal(0)
        normal = unit_price * promo.min_qty * packs
        return max(Decimal(0), normal - Decimal(promo.value) * packs)
    return Decimal(0)


# --------------------------- Resultado con desglose ---------------------------

@dataclass
class LineDiscount:
    """Descuento aplicado a UNA línea por UNA promoción."""
    line_index: int
    promotion_id: UUID
    promotion_name: str
    promotion_type: str
    amount: Decimal          # sin redondear: el redondeo es único, al final
    detail: str              # texto listo para la pantalla del cajero


@dataclass
class PromotionResult:
    lines: list[LineDiscount] = field(default_factory=list)
    total: Decimal = Decimal(0)

    @property
    def promotion_ids(self) -> set[UUID]:
        return {ld.promotion_id for ld in self.lines}

    @property
    def single_promotion_id(self) -> UUID | None:
        """Compatibilidad con `Sale.promotion_id`, que solo admite una. El
        desglose completo vive en `lines`."""
        ids = self.promotion_ids
        return next(iter(ids)) if len(ids) == 1 else None


def _describe(promo: Promotion, amount: Decimal, quantity: int) -> str:
    if promo.type == "percent":
        return f"{promo.name}: {promo.value:g}% de descuento"
    if promo.type == "fixed":
        return f"{promo.name}: descuento de {amount.quantize(Decimal('0.01'))}"
    if promo.type == "qty_price":
        packs = quantity // promo.min_qty
        return f"{promo.name}: {packs} x ({promo.min_qty} por {promo.value})"
    return promo.name


def active_discount_promotions(db: Session, now: datetime) -> list[Promotion]:
    """Promociones automáticas vigentes ahora mismo.

    Filtra estado y fecha de corte **en SQL** (índice
    `ix_promotions_status_ends_at`). Antes se traía la tabla completa y se
    filtraba en Python en cada `GET /menu` y `GET /cart` públicos, y el job de
    medianoche desactiva lo vencido pero no lo borra, así que la tabla solo
    crece.
    """
    today: date = local_now(now).date()
    stmt = (
        select(Promotion)
        .options(selectinload(Promotion.targets))
        .where(
            Promotion.status == "active",
            Promotion.type.in_(AUTO_TYPES),
            or_(Promotion.ends_at.is_(None), Promotion.ends_at >= today),
        )
    )
    return [p for p in db.execute(stmt).scalars().all() if _valid_now(p, now)]


def best_line_discount(
    valid_promos: list[Promotion], product_id, category_id, quantity: int,
    line_total: Decimal, unit_price: Decimal | None = None,
) -> tuple[Decimal, object]:
    """Mejor promoción para una sola línea, entre las ya filtradas por vigencia.

    Criterio: `priority` mayor gana; empate por descuento mayor; empate por
    `created_at` más antiguo. Devuelve `(monto, promotion_id | None)`.
    """
    quantity = int(quantity)
    line_total = Decimal(line_total)
    if unit_price is None:
        unit_price = line_total / quantity if quantity else Decimal(0)
    unit_price = Decimal(unit_price)

    best_key = None
    best_amount = Decimal(0)
    best_id = None
    for p in valid_promos:
        if quantity < p.min_qty:
            continue
        if not _matches(p, product_id, category_id):
            continue
        amount = _line_discount(p, line_total, quantity, unit_price)
        if amount <= 0:
            continue
        key = (p.priority, amount, -p.created_at.timestamp())
        if best_key is None or key > best_key:
            best_key, best_amount, best_id = key, amount, p.id
    return best_amount, best_id


def evaluate_detailed(
    db: Session,
    lines: list[dict],
    now: datetime,
    excluded_promotion_ids: set[UUID] | None = None,
) -> PromotionResult:
    """`lines`: dicts con `product_id`, `category_id`, `quantity`, `line_total` y
    opcionalmente `unit_price` (necesario para `qty_price` cuando la línea lleva
    opciones con recargo; si falta se deriva de `line_total / quantity`).

    `excluded_promotion_ids` implementa "retirar una promoción": el usuario con
    permiso saca una promoción del cobro y el resto se recalcula, en vez de
    perderse el descuento entero.
    """
    excluded = excluded_promotion_ids or set()
    valid = [p for p in active_discount_promotions(db, now) if p.id not in excluded]
    if not valid:
        return PromotionResult()

    by_id = {p.id: p for p in valid}
    result = PromotionResult()
    raw_total = Decimal(0)

    for index, line in enumerate(lines):
        quantity = int(line["quantity"])
        line_total = Decimal(line["line_total"])
        unit_price = line.get("unit_price")
        amount, promo_id = best_line_discount(
            valid, line.get("product_id"), line.get("category_id"),
            quantity, line_total, unit_price,
        )
        if amount > 0 and promo_id is not None:
            promo = by_id[promo_id]
            raw_total += amount
            result.lines.append(LineDiscount(
                line_index=index,
                promotion_id=promo.id,
                promotion_name=promo.name,
                promotion_type=promo.type,
                amount=amount,
                detail=_describe(promo, amount, quantity),
            ))

    result.total = raw_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result


def evaluate(db: Session, lines: list[dict], now: datetime) -> tuple[Decimal, object]:
    """Firma anterior `(descuento_total, promotion_id | None)`.

    Se conserva para que los cuatro caminos de cobro sigan funcionando durante
    la migración a `evaluate_detailed`. Cuando todos consuman el desglose, se
    borra.
    """
    r = evaluate_detailed(db, lines, now)
    return r.total, r.single_promotion_id


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
    """Resuelve la selección explícita de un combo en sus componentes reales, a
    precio normal. El ahorro se aplica después como descuento, sin reescribir el
    `unit_price` de cada línea."""
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
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Variante inactiva en el combo: {variant.id}"
            )
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
    """Agrupa por `combo_id` y descuenta solo los bundles completos, para que una
    anulación parcial deje el remanente a precio normal. Si el combo ya no existe
    o no está vigente, ese grupo no descuenta."""
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
            # `min`, no `setdefault`: si la misma variante llega con dos precios
            # (cambio de catálogo a mitad de sesión), el cliente no paga el alto.
            prev = price_by_variant.get(line.product_variant_id)
            price = Decimal(line.unit_price)
            price_by_variant[line.product_variant_id] = price if prev is None else min(prev, price)

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


# --------------------------- Solapamiento (advertencia, no bloqueo) ---------------------------

def _ranges_overlap(a: Promotion, b: Promotion) -> bool:
    if a.starts_at and b.ends_at and a.starts_at.date() > b.ends_at.date():
        return False
    if b.starts_at and a.ends_at and b.starts_at.date() > a.ends_at.date():
        return False
    return True


def _csv_overlap(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # nulo no restringe, así que se solapa con todo
    return bool({x.strip() for x in a.split(",")} & {x.strip() for x in b.split(",")})


def _times_overlap(a: Promotion, b: Promotion) -> bool:
    if a.start_time is None or b.start_time is None:
        return True
    return _in_time_window(a.start_time, b.start_time, b.end_time) or \
        _in_time_window(b.start_time, a.start_time, a.end_time)


def _scope_overlap(db: Session, a: Promotion, b: Promotion) -> bool:
    if not a.targets or not b.targets:
        return True  # alguna es global
    a_prod = {t.product_id for t in a.targets if t.product_id}
    b_prod = {t.product_id for t in b.targets if t.product_id}
    a_cat = {t.category_id for t in a.targets if t.category_id}
    b_cat = {t.category_id for t in b.targets if t.category_id}
    if a_prod & b_prod or a_cat & b_cat:
        return True
    # Un target de producto choca con un target de categoría si el producto
    # pertenece a esa categoría.
    for prods, cats in ((a_prod, b_cat), (b_prod, a_cat)):
        if prods and cats:
            hit = db.execute(
                select(Product.id).where(Product.id.in_(prods), Product.category_id.in_(cats))
            ).first()
            if hit:
                return True
    return False


def find_overlaps(db: Session, promo: Promotion) -> list[Promotion]:
    """Promociones que pueden competir por la misma línea.

    **Advierte, no bloquea.** Un bloqueo duro haría imposibles los propios casos
    de uso del RF: "10% en todos los granizados" y "20% los martes" se solapan
    los martes sobre los granizados. Para eso existe `priority`.
    """
    candidates = db.execute(
        select(Promotion)
        .options(selectinload(Promotion.targets))
        .where(
            Promotion.id != promo.id,
            Promotion.status.in_(("draft", "active", "paused")),
            Promotion.type.in_(AUTO_TYPES),
        )
    ).scalars().all()

    return [
        c for c in candidates
        if _ranges_overlap(promo, c)
        and _csv_overlap(promo.days_of_week, c.days_of_week)
        and _csv_overlap(promo.days_of_month, c.days_of_month)
        and _times_overlap(promo, c)
        and _scope_overlap(db, promo, c)
    ]


# --------------------------- CRUD ---------------------------

def list_query(search: str | None = None, status_filter: str | None = None) -> Select:
    """Select filtrado/ordenado para `GET /promotions`. Orden por prioridad
    descendente y luego nombre: el admin ve primero la que gana."""
    stmt = (
        select(Promotion)
        .options(selectinload(Promotion.targets), selectinload(Promotion.combo_items))
        .order_by(Promotion.priority.desc(), Promotion.name)
    )
    if status_filter:
        stmt = stmt.where(Promotion.status == status_filter)
    if search:
        stmt = stmt.where(Promotion.name.ilike(f"%{search.strip()}%"))
    return stmt


def _apply_targets(db: Session, promo: Promotion, targets) -> None:
    promo.targets.clear()
    db.flush()
    for t in targets:
        db.add(PromotionTarget(
            promotion_id=promo.id, product_id=t.product_id, category_id=t.category_id
        ))


def _apply_combo_items(db: Session, promo: Promotion, items) -> None:
    promo.combo_items.clear()
    db.flush()
    for c in items:
        db.add(PromotionComboItem(
            promotion_id=promo.id, product_variant_id=c.product_variant_id, quantity=c.quantity,
        ))


def create(db: Session, data) -> Promotion:
    promo = Promotion(
        name=data.name, description=data.description, type=data.type.value,
        value=data.value, status=data.status.value, priority=data.priority,
        starts_at=data.starts_at, ends_at=data.ends_at,
        days_of_week=data.days_of_week, days_of_month=data.days_of_month,
        start_time=data.start_time, end_time=data.end_time, min_qty=data.min_qty,
    )
    db.add(promo)
    db.flush()
    _apply_targets(db, promo, data.targets)
    _apply_combo_items(db, promo, data.combo_items)
    db.flush()
    return promo


def update(db: Session, promo: Promotion, data) -> Promotion:
    """Campos escalares. `model_fields_set` (no `is not None`): el admin debe
    poder limpiar un campo opcional enviando `null` explícito."""
    provided = data.model_fields_set
    for field_name in ("name", "description", "value", "priority", "starts_at", "ends_at",
                       "days_of_week", "days_of_month", "start_time", "end_time", "min_qty"):
        if field_name in provided:
            setattr(promo, field_name, getattr(data, field_name))

    # Las reglas que dependen del `type` no se pueden validar en `PromotionUpdate`
    # (no lleva `type`), así que se validan aquí contra el tipo real. Sin esto,
    # un PATCH `value=500` sobre un percent llegaba a la base y el CHECK lo
    # convertía en un 500.
    if promo.type == "percent" and Decimal(promo.value) > 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un descuento porcentual no puede superar 100",
        )
    if promo.type == "qty_price" and promo.min_qty < 2:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "qty_price requiere min_qty >= 2 (el tamaño del paquete)",
        )
    if (promo.start_time is None) != (promo.end_time is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "start_time y end_time deben configurarse juntos",
        )
    db.flush()
    return promo


def update_shape(db: Session, promo: Promotion, data) -> Promotion:
    """Cambia `type`, `targets` o `combo_items`. Solo en `draft`: una promoción
    que ya estuvo activa pudo explicar el descuento de una venta, y cambiarle la
    forma reescribiría esa historia. Para eso está `duplicate`."""
    if promo.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo una promoción en borrador puede cambiar de tipo o alcance. "
            "Duplícala, edita la copia y finaliza la original.",
        )
    if data.type is not None:
        promo.type = data.type.value
    if data.targets is not None:
        _apply_targets(db, promo, data.targets)
    if data.combo_items is not None:
        _apply_combo_items(db, promo, data.combo_items)
    db.flush()
    db.refresh(promo)

    if promo.type == "combo" and len({c.product_variant_id for c in promo.combo_items}) < 2:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un combo requiere al menos 2 productos distintos en combo_items",
        )
    if promo.type != "combo" and promo.combo_items:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "combo_items solo aplica a promociones type=combo",
        )
    return promo


def change_status(db: Session, promo: Promotion, new_status: str) -> Promotion:
    if new_status == promo.status:
        return promo
    if new_status not in PROMOTION_TRANSITIONS[promo.status]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Transición no permitida: {promo.status} -> {new_status}",
        )
    if new_status == "active" and promo.type == "combo" and len(promo.combo_items) < 2:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No se puede activar un combo sin al menos 2 componentes",
        )
    promo.status = new_status
    db.flush()
    return promo


def duplicate(db: Session, promo: Promotion, new_name: str) -> Promotion:
    """Copia en `draft` con targets y componentes. Es la salida al hecho de que
    una promoción activa no pueda cambiar de forma: duplicar, editar la copia,
    activarla y finalizar la original."""
    copy = Promotion(
        name=new_name, description=promo.description, type=promo.type,
        value=promo.value, status="draft", priority=promo.priority,
        starts_at=promo.starts_at, ends_at=promo.ends_at,
        days_of_week=promo.days_of_week, days_of_month=promo.days_of_month,
        start_time=promo.start_time, end_time=promo.end_time, min_qty=promo.min_qty,
    )
    db.add(copy)
    db.flush()
    for t in promo.targets:
        db.add(PromotionTarget(
            promotion_id=copy.id, product_id=t.product_id, category_id=t.category_id
        ))
    for c in promo.combo_items:
        db.add(PromotionComboItem(
            promotion_id=copy.id, product_variant_id=c.product_variant_id, quantity=c.quantity,
        ))
    db.flush()
    db.refresh(copy)
    return copy
