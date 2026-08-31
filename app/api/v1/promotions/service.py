"""Promociones — spec 063: modelo por **conjunto explícito de variantes**.

Reemplaza el motor de las specs 012 (`evaluate` / `evaluate_detailed`) y 040
(`combined_discount_detailed` / `presentation_package_discount_for_lines`) por
**un único motor** `evaluate_variant_sets` (contracts/motor-y-persistencia.md §2):

- una promoción = `(type ∈ {percent, package_price}, value, min_qty)` + un
  conjunto de variantes (`promotion_variants`);
- por promoción vigente, se reúnen todas las unidades del pedido cuyas variantes
  pertenecen al conjunto, se arman `total // min_qty` **grupos completos** por
  **consumo codicioso descendente de precio**, se descuenta solo esos grupos y
  se reparte el descuento repartiendo el **importe cobrado** (residuo a la
  variante de id más alto);
- **no hay reconciliación**: el bloqueo de solape real de FR-014
  (`_guard_variant_overlap`) garantiza que una variante nunca está en dos
  promociones vigentes al mismo instante, así que `priority` desaparece.

`_tz`, `local_now`, `_in_time_window`, `_valid_now` se **conservan sin cambio de
cuerpo** (A-57 intacto).
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.core.config import settings
from app.core.models import Tenant
from app.core.timezone import resolve_timezone
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.promotion import PROMOTION_TRANSITIONS, Promotion, PromotionVariant

# Tipos **vivos** que el motor evalúa. Las promociones que la migración `063a`
# dejó `finished` con un `type` viejo quedan fuera por `status != "active"`.
LIVE_TYPES = ("percent", "package_price")


# --------------------------- Hora local (sin cambio, A-57) ---------------------------

def _tz(tenant: Tenant | None = None) -> ZoneInfo:
    """Zona horaria de evaluación. Si el caller ya resolvió el `tenant`
    (spec 030, Historia 4/A-46), usa la suya; si no, conserva el respaldo
    global de instancia (`TENANT_TIMEZONE`)."""
    if tenant is not None:
        return resolve_timezone(tenant)
    return ZoneInfo(settings.TENANT_TIMEZONE)


def local_now(now: datetime | None = None) -> datetime:
    """`now` como hora local **naive** del tenant. Acepta aware (convierte) o
    naive (asume ya local)."""
    if now is None:
        return datetime.now(_tz()).replace(tzinfo=None)
    if now.tzinfo is not None:
        return now.astimezone(_tz()).replace(tzinfo=None)
    return now


def _in_time_window(current: time, start: time | None, end: time | None) -> bool:
    """Ventana horaria, con soporte para cruce de medianoche."""
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
    """`now` puede venir en UTC o local; se normaliza a hora local del tenant.
    A-57 (atribución de día al cruzar medianoche) intacto."""
    now = local_now(now)
    if promo.status != "active":
        return False
    if promo.starts_at is not None and now < promo.starts_at:
        return False

    ref = now
    if (
        promo.start_time is not None
        and promo.end_time is not None
        and promo.start_time > promo.end_time
        and now.time() <= promo.end_time
    ):
        ref = now - timedelta(days=1)

    if promo.ends_at is not None and ref.date() > promo.ends_at.date():
        return False
    if promo.days_of_week:
        allowed = {d.strip() for d in promo.days_of_week.split(",") if d.strip()}
        if str(ref.weekday()) not in allowed:  # 0=lunes..6=domingo
            return False
    return _in_time_window(now.time(), promo.start_time, promo.end_time)


def _line_get(line, name: str, default=None):
    """Acceso uniforme a una línea, sea un `SaleLine` (atributos) o un dict
    enriquecido de `promo_lines_for` (claves)."""
    if isinstance(line, dict):
        return line.get(name, default)
    return getattr(line, name, default)


def _money(value: Decimal) -> str:
    """`$12.000` — formato de pesos colombianos con separador de miles."""
    return "$" + f"{int(value):,}".replace(",", ".")


# --------------------------- Motor por conjunto ---------------------------

@dataclass
class AppliedPromotion:
    promotion_id: UUID
    name: str            # snapshot: sobrevive al borrado de la promoción
    amount: Decimal      # descuento agregado de ESTA promoción en ESTE cobro (>= 0)


@dataclass
class SetDiscountResult:
    total: Decimal = Decimal(0)                  # Σ by_line, redondeado una sola vez
    by_line: dict = field(default_factory=dict)  # line_index -> descuento (Decimal)
    applied: list = field(default_factory=list)  # list[AppliedPromotion]

    @property
    def single_promotion_id(self) -> UUID | None:
        return self.applied[0].promotion_id if len(self.applied) == 1 else None


def active_variant_set_promotions(db: Session, now: datetime) -> list[Promotion]:
    """Promociones `percent` / `package_price` **activas y vigentes ahora**
    (`_valid_now`, hora local del tenant). Hermana de la vieja
    `active_discount_promotions`; usa el índice `ix_promotions_status_ends_at`."""
    today: date = local_now(now).date()
    stmt = (
        select(Promotion)
        .options(selectinload(Promotion.variants))
        .where(
            Promotion.status == "active",
            Promotion.type.in_(LIVE_TYPES),
            or_(Promotion.ends_at.is_(None), Promotion.ends_at >= today),
        )
    )
    return [p for p in db.execute(stmt).scalars().all() if _valid_now(p, now)]


def _unit_sort_key(unit) -> tuple:
    """Orden de consumo codicioso (FR-008): precio unitario **descendente**,
    desempate `product_variant_id` ascendente y luego `line_id` ascendente. La
    comparación de `str(uuid)` coincide con la nativa (`.int`) por el ancho fijo
    del formato hex. Nunca usa la posición de la línea."""
    _idx, unit_price, pv_id, line_id = unit
    return (-unit_price, str(pv_id), str(line_id) if line_id is not None else "")


def _greedy_units(eligible_units: list, min_qty: int) -> list[list]:
    """Trocea las `grupos * min_qty` unidades más caras en bloques consecutivos
    de `min_qty` (FR-007, FR-008). El remanente (a precio normal) no se devuelve."""
    grupos = len(eligible_units) // min_qty
    if grupos == 0:
        return []
    ordenadas = sorted(eligible_units, key=_unit_sort_key)
    en_grupos = ordenadas[: grupos * min_qty]
    return [en_grupos[i * min_qty:(i + 1) * min_qty] for i in range(grupos)]


def _distribute_group_discount(group_units: list, discount: Decimal) -> dict:
    """Reparte `discount` (ya redondeado a peso, FR-006) entre las líneas que
    aportan unidades a **un** grupo completo, repartiendo el importe cobrado
    (FR-008a): `cobrado_L = floor(aporte_L - discount * aporte_L / aporte_total)`;
    los pesos que falten se suman al `cobrado_L` de la línea cuya variante tiene
    el `product_variant_id` más alto (desempate: `line_id` más alto).
    Devuelve `{line_index: descuento_de_L}` — la suma es `discount` exacta."""
    aporte: dict = {}
    info: dict = {}  # line_index -> (pv_id, line_id)
    for line_index, unit_price, pv_id, line_id in group_units:
        aporte[line_index] = aporte.get(line_index, Decimal(0)) + unit_price
        info[line_index] = (pv_id, line_id)

    aporte_total = sum(aporte.values(), Decimal(0))
    objetivo = aporte_total - discount

    cobrado: dict = {}
    for line_index, aporte_l in aporte.items():
        cobrado[line_index] = (
            aporte_l - discount * aporte_l / aporte_total
        ).to_integral_value(rounding=ROUND_FLOOR)

    falta = objetivo - sum(cobrado.values(), Decimal(0))
    if falta != 0:
        top = max(
            aporte,
            key=lambda li: (
                str(info[li][0]),
                str(info[li][1]) if info[li][1] is not None else "",
            ),
        )
        cobrado[top] += falta

    return {li: aporte[li] - cobrado[li] for li in aporte if aporte[li] != cobrado[li]}


def evaluate_variant_sets(db: Session, promo_lines: list, now: datetime) -> SetDiscountResult:
    """Algoritmo normativo de contracts/motor-y-persistencia.md §2."""
    result = SetDiscountResult()
    promos = active_variant_set_promotions(db, now)
    if not promos:
        return result

    by_line: dict = {}
    applied_amount: dict = {}   # promo_id -> [name, Decimal]

    for p in promos:
        conjunto = {v.product_variant_id for v in p.variants}
        if not conjunto:
            continue

        units: list = []
        for idx, line in enumerate(promo_lines):
            pv_id = _line_get(line, "product_variant_id")
            if pv_id not in conjunto:
                continue
            if not _line_get(line, "_variant_active", True):  # FR-011
                continue
            if _line_get(line, "combo_id") is not None:       # defensivo
                continue
            unit_price = Decimal(_line_get(line, "unit_price", 0))
            line_id = _line_get(line, "line_id")
            for _ in range(int(_line_get(line, "quantity", 0))):
                units.append((idx, unit_price, pv_id, line_id))

        promo_amount = Decimal(0)
        for block in _greedy_units(units, p.min_qty):
            normal_g = sum((u[1] for u in block), Decimal(0))
            if p.type == "package_price":
                descuento_g = max(Decimal(0), normal_g - Decimal(p.value))
            else:  # percent
                descuento_g = (
                    normal_g * Decimal(p.value) / Decimal(100)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if descuento_g <= 0:
                continue
            for line_index, d in _distribute_group_discount(block, descuento_g).items():
                by_line[line_index] = by_line.get(line_index, Decimal(0)) + d
                promo_amount += d

        if promo_amount > 0:
            applied_amount[p.id] = [p.name, promo_amount]

    result.by_line = by_line
    result.total = sum(by_line.values(), Decimal(0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    result.applied = [
        AppliedPromotion(
            promotion_id=pid, name=nm,
            amount=amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        )
        for pid, (nm, amt) in sorted(applied_amount.items(), key=lambda kv: str(kv[0]))
    ]
    return result


def applied_to_dicts(applied: list) -> list[dict]:
    """`SetDiscountResult.applied` -> lista serializable para `applied_promotions`
    (JSONB): `[{promotion_id, name, amount}]` (contract §5)."""
    return [
        {"promotion_id": str(a.promotion_id), "name": a.name, "amount": str(a.amount)}
        for a in applied
    ]


def menu_unit_discount(promos: list, variant_id, unit_price) -> Decimal | None:
    """Descuento por variante para el menú público (contracts/superficies-consumo.md
    §1): solo `percent` con `min_qty == 1` baja el precio unitario; `package_price`
    y `percent` con `min_qty > 1` -> `None` (depende de cuántas unidades combinadas
    haya, que en el menú sin carrito no existen — igual que hoy `qty_price`)."""
    for p in promos:
        if p.type != "percent" or p.min_qty != 1:
            continue
        if variant_id in {v.product_variant_id for v in p.variants}:
            return Decimal(unit_price) * Decimal(p.value) / Decimal(100)
    return None


def variant_set_condition_text(promo: Promotion) -> str | None:
    """Condición en lenguaje llano, español de Colombia (contract §4). `None`
    para una promoción `finished` de tipo viejo."""
    if promo.type not in LIVE_TYPES:
        return None
    n = len(promo.variants)
    value = Decimal(promo.value)
    if promo.type == "package_price":
        if promo.min_qty > 1:
            return f"Llevando {promo.min_qty} de estas {n} variantes pagas {_money(value)}"
        return f"Cada una de estas {n} variantes a {_money(value)}"
    # `10.00` -> `10`, `12.50` -> `12.5` (`{value:g}` no despoja los ceros de un
    # Decimal; `.rstrip("0")` a secas convertiría `10` en `1`).
    pct = f"{value:f}"
    if "." in pct:
        pct = pct.rstrip("0").rstrip(".")
    if promo.min_qty == 1:
        return f"{pct}% en estas {n} variantes"
    return f"{pct}% llevando {promo.min_qty} de estas {n} variantes"


# --------------------------- Solape real (bloqueo, FR-014) ---------------------------

def _ranges_overlap(a: Promotion, b: Promotion) -> bool:
    if a.starts_at and b.ends_at and a.starts_at.date() > b.ends_at.date():
        return False
    if b.starts_at and a.ends_at and b.starts_at.date() > a.ends_at.date():
        return False
    return True


def _csv_overlap(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True  # dimensión abierta = cubre todo su dominio
    return bool({x.strip() for x in a.split(",")} & {x.strip() for x in b.split(",")})


def _times_overlap(a: Promotion, b: Promotion) -> bool:
    if a.start_time is None or b.start_time is None:
        return True
    return _in_time_window(a.start_time, b.start_time, b.end_time) or \
        _in_time_window(b.start_time, a.start_time, a.end_time)


def _guard_variant_overlap(db: Session, promo: Promotion, variant_ids) -> None:
    """FR-014 / FR-014a (contracts/administracion-promociones.md §2): rechaza con
    **409** si el conjunto comparte >= 1 variante con otra promoción en
    `draft`/`active`/`paused` **y** sus rangos de fecha ∧ días ∧ horas se
    intersectan simultáneamente. Dimensión no definida = cubre todo su dominio."""
    vset = set(variant_ids)
    if not vset:
        return
    candidates = db.execute(
        select(Promotion)
        .options(selectinload(Promotion.variants))
        .where(
            Promotion.id != promo.id,
            Promotion.status.in_(("draft", "active", "paused")),
        )
    ).scalars().all()

    conflicts: list[dict] = []
    for c in candidates:
        shared = vset & {v.product_variant_id for v in c.variants}
        if not shared:
            continue
        if not (_ranges_overlap(promo, c)
                and _csv_overlap(promo.days_of_week, c.days_of_week)
                and _times_overlap(promo, c)):
            continue
        conflicts.append({
            "promotion_id": str(c.id),
            "promotion_name": c.name,
            "variant_ids": sorted(str(v) for v in shared),
        })

    if conflicts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    "Otra promoción activa ya cubre esta(s) variante(s) en un "
                    "horario que se cruza"
                ),
                "conflicts": conflicts,
            },
        )


def _guard_package_is_discount(db: Session, promo: Promotion) -> None:
    """FR-016 / SC-002 (research.md D16): `type == "package_price"` y
    `value >= min_qty × (menor price entre las variantes del conjunto, activas o
    no)` -> **409**."""
    if promo.type != "package_price":
        return
    variant_ids = [v.product_variant_id for v in promo.variants]
    if not variant_ids:
        return
    rows = db.execute(
        select(ProductVariant.id, ProductVariant.price)
        .where(ProductVariant.id.in_(variant_ids))
    ).all()
    if not rows:
        return
    cheapest_id, cheapest_price = min(rows, key=lambda r: Decimal(r[1]))
    if Decimal(promo.value) >= promo.min_qty * Decimal(cheapest_price):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    "Con este precio de paquete la promoción no representa un descuento"
                ),
                "value": str(promo.value),
                "min_qty": promo.min_qty,
                "cheapest_unit_price": str(cheapest_price),
                "variant_id": str(cheapest_id),
            },
        )


# --------------------------- CRUD ---------------------------

def list_query(
    search: str | None = None,
    status_filter: str | None = None,
    closed_by_refactor: bool | None = None,
) -> Select:
    """Select filtrado/ordenado para `GET /promotions`. Orden por `name`
    (ya no `priority`, A-58)."""
    stmt = (
        select(Promotion)
        .options(
            selectinload(Promotion.variants).selectinload(PromotionVariant.product_variant)
        )
        .order_by(Promotion.name)
    )
    if status_filter:
        stmt = stmt.where(Promotion.status == status_filter)
    if search:
        stmt = stmt.where(Promotion.name.ilike(f"%{search.strip()}%"))
    if closed_by_refactor is True:
        stmt = stmt.where(Promotion.closed_by_refactor_at.is_not(None))
    elif closed_by_refactor is False:
        stmt = stmt.where(Promotion.closed_by_refactor_at.is_(None))
    return stmt


def _apply_variant_set(db: Session, promo: Promotion, variant_ids) -> None:
    """FR-001: valida (no vacía, sin repetidos, cada uuid existe y es del tenant)
    y puebla `promotion_variants`."""
    if not variant_ids:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Una promoción necesita al menos una variante",
        )
    seen: set = set()
    for vid in variant_ids:
        if vid in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "El conjunto de variantes no puede repetir una variante",
            )
        seen.add(vid)

    existentes = set(db.execute(
        select(ProductVariant.id).where(ProductVariant.id.in_(seen))
    ).scalars().all())
    faltan = seen - existentes
    if faltan:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Variante no encontrada en el catálogo del tenant",
        )

    promo.variants.clear()
    db.flush()
    for vid in variant_ids:
        db.add(PromotionVariant(promotion_id=promo.id, product_variant_id=vid))
    db.flush()


def _revalidate_type_rules(promo: Promotion) -> None:
    if promo.type == "percent" and Decimal(promo.value) > 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Un descuento porcentual no puede superar 100",
        )
    if promo.type == "package_price" and Decimal(promo.value) <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "El precio de paquete debe ser mayor que 0",
        )


def create(db: Session, data) -> Promotion:
    promo = Promotion(
        name=data.name, description=data.description, type=data.type.value,
        value=data.value, status=data.status.value,
        starts_at=data.starts_at, ends_at=data.ends_at,
        days_of_week=data.days_of_week,
        start_time=data.start_time, end_time=data.end_time, min_qty=data.min_qty,
    )
    db.add(promo)
    db.flush()
    _apply_variant_set(db, promo, data.variant_ids)
    db.refresh(promo)
    _guard_package_is_discount(db, promo)
    _guard_variant_overlap(db, promo, data.variant_ids)
    db.flush()
    return promo


def update(db: Session, promo: Promotion, data) -> Promotion:
    """Campos escalares. `value` / `min_qty` bloqueados fuera de `draft`
    (FR-018), evaluado contra `promo.status` real."""
    provided = data.model_fields_set
    if ("value" in provided or "min_qty" in provided) and promo.status != "draft":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Duplica la promoción para cambiar el valor o la cantidad",
        )
    for field_name in ("name", "description", "value", "ends_at",
                       "days_of_week", "start_time", "end_time", "min_qty"):
        if field_name in provided:
            setattr(promo, field_name, getattr(data, field_name))

    _revalidate_type_rules(promo)
    if (promo.start_time is None) != (promo.end_time is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "start_time y end_time deben configurarse juntos",
        )
    db.flush()
    return promo


def update_shape(db: Session, promo: Promotion, data) -> Promotion:
    """Cambia `type` / `variant_ids`. Solo en `draft` (FR-018)."""
    if promo.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Solo una promoción en borrador puede cambiar de tipo o de conjunto. "
            "Duplícala, edita la copia y finaliza la original.",
        )
    if data.type is not None:
        promo.type = data.type.value
    if data.variant_ids is not None:
        _apply_variant_set(db, promo, data.variant_ids)
    db.flush()
    db.refresh(promo)

    _revalidate_type_rules(promo)
    _guard_package_is_discount(db, promo)
    _guard_variant_overlap(db, promo, [v.product_variant_id for v in promo.variants])
    return promo


def change_status(db: Session, promo: Promotion, new_status: str) -> Promotion:
    if new_status == promo.status:
        return promo
    if new_status not in PROMOTION_TRANSITIONS[promo.status]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Transición no permitida: {promo.status} -> {new_status}",
        )
    if new_status == "active":
        # Una promo creada en `draft` sin conflicto puede chocar al activar.
        if not promo.variants:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Una promoción necesita al menos una variante",
            )
        _guard_package_is_discount(db, promo)
        _guard_variant_overlap(db, promo, [v.product_variant_id for v in promo.variants])
    promo.status = new_status
    db.flush()
    return promo


def duplicate(db: Session, promo: Promotion, new_name: str) -> Promotion:
    """Copia en `draft` con el mismo tipo/valor/`min_qty`/conjunto/vigencia,
    nombre distinto (FR-017). El solape de FR-014 se revalida al **activar** la
    copia, no al duplicar."""
    copy = Promotion(
        name=new_name, description=promo.description, type=promo.type,
        value=promo.value, status="draft",
        starts_at=promo.starts_at, ends_at=promo.ends_at,
        days_of_week=promo.days_of_week,
        start_time=promo.start_time, end_time=promo.end_time, min_qty=promo.min_qty,
    )
    db.add(copy)
    db.flush()
    for v in promo.variants:
        db.add(PromotionVariant(
            promotion_id=copy.id, product_variant_id=v.product_variant_id,
        ))
    db.flush()
    db.refresh(copy)
    return copy


# --------------------------- Serialización ---------------------------

def serialize_promotion(db: Session, promo: Promotion) -> dict:
    """`PromotionResponse` con `variants` (descripción + precio normal vigente,
    FR-005) y `condition_text` resueltos."""
    variant_ids = [v.product_variant_id for v in promo.variants]
    rows = db.execute(
        select(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(ProductVariant.id.in_(variant_ids))
    ).all() if variant_ids else []
    by_id = {v.id: (v, p) for v, p in rows}

    variants = []
    for vid in variant_ids:
        v, p = by_id.get(vid, (None, None))
        if v is None:
            continue
        variants.append({
            "product_variant_id": vid,
            "description": f"{p.name} - {v.name}" if p else v.name,
            "unit_price": Decimal(v.price),
        })

    return {
        "id": promo.id,
        "name": promo.name,
        "description": promo.description,
        "type": promo.type,
        "value": promo.value,
        "status": promo.status,
        "starts_at": promo.starts_at,
        "ends_at": promo.ends_at,
        "days_of_week": promo.days_of_week,
        "start_time": promo.start_time,
        "end_time": promo.end_time,
        "min_qty": promo.min_qty,
        "closed_by_refactor_at": promo.closed_by_refactor_at,
        "condition_text": variant_set_condition_text(promo),
        "variants": variants,
    }
