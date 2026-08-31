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
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql import Select

from app.core.config import settings
from app.core.crud import get_or_404
from app.core.models import Tenant
from app.core.timezone import resolve_timezone
from app.models.presentation import Presentation
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.promotion import (
    Promotion, PromotionComboItem, PromotionPresentationRule, PromotionTarget,
    PROMOTION_TRANSITIONS,
)

# Tipos que participan del motor automático. `combo` se selecciona
# explícitamente y va por `combo_discount_for_lines`.
AUTO_TYPES = ("percent", "fixed", "qty_price")


# --------------------------- Hora local ---------------------------

def _tz(tenant: Tenant | None = None) -> ZoneInfo:
    """Zona horaria de evaluación. Si el caller ya resolvió el `tenant`
    (spec 030, Historia 4/A-46), usa la suya; si no, conserva el respaldo
    global de instancia (`TENANT_TIMEZONE`) — retrocompatible con los
    callers que todavía no lo pasan."""
    if tenant is not None:
        return resolve_timezone(tenant)
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

    # FR-004 / CL-8 (spec 040, A-55): cuando la ventana cruza la medianoche
    # (`start_time > end_time`) y estamos en el tramo posterior a la medianoche
    # (`now.time() <= end_time`), el día de aplicación es el día en que INICIA la
    # ventana — la fecha de referencia para `days_of_week` y `ends_at` retrocede
    # 24 h. `starts_at` y `start_time` no cambian. Afecta a TODOS los tipos de
    # promoción (este chequeo es compartido).
    ref = now
    if (
        promo.start_time is not None
        and promo.end_time is not None
        and promo.start_time > promo.end_time
        and now.time() <= promo.end_time
    ):
        ref = now - timedelta(days=1)

    # `ends_at` llega como medianoche del día elegido en el selector "Hasta":
    # se compara por fecha para que "Hasta 04/08" cubra el 04/08 completo.
    if promo.ends_at is not None and ref.date() > promo.ends_at.date():
        return False
    if promo.days_of_week:
        allowed = {d.strip() for d in promo.days_of_week.split(",") if d.strip()}
        if str(ref.weekday()) not in allowed:  # 0=lunes..6=domingo
            return False
    return _in_time_window(now.time(), promo.start_time, promo.end_time)


def _matching_target(promo: Promotion, product_id, category_id):
    """`(aplica, target)` para una línea. Sin targets = global, sin target.

    **El de producto gana al de categoría.** Antes daba igual cuál se
    encontrara primero porque el precio era uno solo; ahora el target elegido
    decide el precio del paquete, así que "toda la categoría a $10.000 salvo la
    Grande a $12.000" depende de que el más específico mande.
    """
    if not promo.targets:
        return True, None

    por_categoria = None
    for t in promo.targets:
        if t.product_id is not None and t.product_id == product_id:
            return True, t
        if t.category_id is not None and t.category_id == category_id:
            por_categoria = t
    return (True, por_categoria) if por_categoria is not None else (False, None)


def _pack_terms(promo: Promotion, target) -> tuple[int, Decimal] | None:
    """Tamaño y precio del paquete, que viven **solo en el destino**.

    Devuelve `None` si el destino no los define — o si no hay destino, como en
    un `qty_price` global. Antes se caía a los de la promoción, pero desde que
    el formulario ya no pide un "paquete por defecto" ese campo vale 0, y caer
    a él descontaría la línea entera (`normal - 0 x packs`). Sin precio no hay
    descuento: el fallo seguro en vez del caro.
    """
    if target is None or target.value is None or target.min_qty is None:
        return None
    return target.min_qty, Decimal(target.value)


def _line_discount(promo: Promotion, target, line_total: Decimal, quantity: int,
                   unit_price: Decimal) -> Decimal:
    if promo.type == "percent":
        return line_total * Decimal(promo.value) / Decimal(100)
    if promo.type == "fixed":
        return min(Decimal(promo.value), line_total)
    if promo.type == "qty_price":
        # Solo paquetes completos; el remanente se cobra a precio normal. Misma
        # semántica de "bundle completo" que los combos, para que una anulación
        # parcial degrade suave en vez de romper el cálculo.
        terms = _pack_terms(promo, target)
        if terms is None:
            return Decimal(0)
        pack, price = terms
        packs = quantity // pack
        if packs <= 0:
            return Decimal(0)
        normal = unit_price * pack * packs
        return max(Decimal(0), normal - price * packs)
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


def _describe(promo: Promotion, amount: Decimal, quantity: int, target=None) -> str:
    if promo.type == "percent":
        return f"{promo.name}: {promo.value:g}% de descuento"
    if promo.type == "fixed":
        return f"{promo.name}: descuento de {amount.quantize(Decimal('0.01'))}"
    if promo.type == "qty_price":
        # Con precio por target, el texto del cajero tiene que decir el paquete
        # que se aplicó de verdad. Solo se llama con descuento > 0, así que aquí
        # los términos existen; el `or` es defensivo, no un caso esperado.
        terms = _pack_terms(promo, target)
        if terms is None:
            return promo.name
        pack, price = terms
        packs = quantity // pack
        return f"{promo.name}: {packs} x ({pack} por {price})"
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


def _best_line_match(
    valid_promos: list[Promotion], product_id, category_id, quantity: int,
    line_total: Decimal, unit_price: Decimal | None = None,
):
    """`(monto, promo | None, target | None)` para una línea.

    Devuelve también el target porque el precio del paquete puede salir de él, y
    tanto el desglose del cajero como el propio cálculo lo necesitan.
    """
    quantity = int(quantity)
    line_total = Decimal(line_total)
    if unit_price is None:
        unit_price = line_total / quantity if quantity else Decimal(0)
    unit_price = Decimal(unit_price)

    best_key = None
    best = (Decimal(0), None, None)
    for p in valid_promos:
        aplica, target = _matching_target(p, product_id, category_id)
        if not aplica:
            continue
        # El mínimo se mide contra el paquete del destino: "3 Pequeñas por
        # $20.000" corta en 3 aunque otro destino de la misma promoción pida 2.
        if p.type == "qty_price":
            terms = _pack_terms(p, target)
            if terms is None:
                continue  # destino sin precio: no descuenta
            minimo = terms[0]
        else:
            minimo = p.min_qty
        if quantity < minimo:
            continue
        amount = _line_discount(p, target, line_total, quantity, unit_price)
        if amount <= 0:
            continue
        key = (p.priority, amount, -p.created_at.timestamp())
        if best_key is None or key > best_key:
            best_key, best = key, (amount, p, target)
    return best


def best_line_discount(
    valid_promos: list[Promotion], product_id, category_id, quantity: int,
    line_total: Decimal, unit_price: Decimal | None = None,
) -> tuple[Decimal, object]:
    """Mejor promoción para una sola línea, entre las ya filtradas por vigencia.

    Criterio: `priority` mayor gana; empate por descuento mayor; empate por
    `created_at` más antiguo. Devuelve `(monto, promotion_id | None)` — firma
    que consumen el menú público y el carrito.
    """
    amount, promo, _ = _best_line_match(
        valid_promos, product_id, category_id, quantity, line_total, unit_price
    )
    return amount, (promo.id if promo is not None else None)


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

    result = PromotionResult()
    raw_total = Decimal(0)

    for index, line in enumerate(lines):
        quantity = int(line["quantity"])
        line_total = Decimal(line["line_total"])
        unit_price = line.get("unit_price")
        amount, promo, target = _best_line_match(
            valid, line.get("product_id"), line.get("category_id"),
            quantity, line_total, unit_price,
        )
        if amount > 0 and promo is not None:
            raw_total += amount
            result.lines.append(LineDiscount(
                line_index=index,
                promotion_id=promo.id,
                promotion_name=promo.name,
                promotion_type=promo.type,
                amount=amount,
                detail=_describe(promo, amount, quantity, target),
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


def _line_get(line, name: str, default=None):
    """Acceso uniforme a una línea, sea un `SaleLine` (atributos) o un dict
    enriquecido de `promo_lines_for` (claves). Deja intacto el camino `SaleLine`."""
    if isinstance(line, dict):
        return line.get(name, default)
    return getattr(line, name, default)


def combo_discount_for_lines(db: Session, lines: list, now: datetime) -> Decimal:
    """Agrupa por `combo_id` y descuenta solo los bundles completos, para que una
    anulación parcial deje el remanente a precio normal. Si el combo ya no existe
    o no está vigente, ese grupo no descuenta."""
    by_combo: dict[UUID, list] = {}
    for line in lines:
        combo_id = _line_get(line, "combo_id")
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
            pv_id = _line_get(line, "product_variant_id")
            qty_by_variant[pv_id] = qty_by_variant.get(pv_id, 0) + _line_get(line, "quantity", 0)
            # `min`, no `setdefault`: si la misma variante llega con dos precios
            # (cambio de catálogo a mitad de sesión), el cliente no paga el alto.
            prev = price_by_variant.get(pv_id)
            price = Decimal(_line_get(line, "unit_price", 0))
            price_by_variant[pv_id] = price if prev is None else min(prev, price)

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


# --------------------------- Paquetes por presentación (spec 040) ---------------------------

@dataclass
class PresentationDiscountResult:
    """Descuento por presentación con desglose por línea (FR-011, SC-005)."""
    total: Decimal = Decimal(0)
    by_line: dict = field(default_factory=dict)      # line_index -> descuento (Decimal)
    promotion_ids: set = field(default_factory=set)  # promociones que descontaron alguna línea


@dataclass
class CombinedDiscountResult:
    total: Decimal = Decimal(0)
    promotion_id: UUID | None = None


def active_presentation_promotions(db: Session, now: datetime) -> list[Promotion]:
    """Promociones `qty_price_presentation` **activas y vigentes ahora**
    (`_valid_now`, hora local del tenant). El tipo queda fuera de `AUTO_TYPES`,
    así que `active_discount_promotions` no las trae — esta es su consulta
    hermana (research.md D5)."""
    today: date = local_now(now).date()
    stmt = (
        select(Promotion)
        .options(
            selectinload(Promotion.presentation_rules).selectinload(
                PromotionPresentationRule.presentation
            )
        )
        .where(
            Promotion.status == "active",
            Promotion.type == QTY_PRICE_PRESENTATION,
            or_(Promotion.ends_at.is_(None), Promotion.ends_at >= today),
        )
    )
    return [p for p in db.execute(stmt).scalars().all() if _valid_now(p, now)]


def _presentation_reference_unit_price(eligible_lines: list) -> Decimal:
    """FR-011 / FR-017: el precio unitario normal de una presentación es ÚNICO —
    el **menor** `unit_price` vigente entre las variantes elegibles que aportan
    unidades a esa presentación en el pedido. Nunca variante por variante (mismo
    criterio `min(...)` que `combo_discount_for_lines`)."""
    return min(Decimal(_line_get(l, "unit_price", 0)) for l in eligible_lines)


def _unit_sort_key(line) -> tuple:
    """Orden determinista de una unidad (FR-011, CL-9, SC-005): por el valor del
    UUID de la variante y, si empatan, por el `id` de la fila de línea. Nunca por
    la posición de la línea en la lista."""
    variant_id = _line_get(line, "product_variant_id")
    line_id = _line_get(line, "line_id")
    return (str(variant_id) if variant_id is not None else "",
            str(line_id) if line_id is not None else "")


def _rule_discount_by_line(rule, eligible: list) -> dict:
    """Desglose `{line_index: descuento}` de una sola regla sobre sus líneas
    elegibles (research.md D5 pasos b-h). `eligible`: lista de `(line_index, línea)`.

    Reparto determinista (FR-011, CL-9): las unidades se ordenan ascendente por
    `(variante, línea)`; las `leftover` unidades más altas se cobran a `precio_ref`
    y la unidad de paquete más alta lleva el residuo del redondeo.
    """
    total_units = sum(int(_line_get(l, "quantity", 0)) for _, l in eligible)
    packs = total_units // rule.min_qty
    if packs <= 0:  # FR-010 / FR-012: sin paquete completo, no descuenta
        return {}

    ref = _presentation_reference_unit_price([l for _, l in eligible])
    units_in_packs = packs * rule.min_qty
    leftover = total_units - units_in_packs
    per_pack_unit = (Decimal(rule.pack_price) / rule.min_qty).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    residual = Decimal(rule.pack_price) * packs - per_pack_unit * units_in_packs

    units: list[int] = []  # line_index por unidad
    for i, l in sorted(eligible, key=lambda t: _unit_sort_key(t[1])):
        units.extend([i] * int(_line_get(l, "quantity", 0)))

    charged_by_line: dict = {}
    for pos, line_index in enumerate(units):
        if pos >= total_units - leftover:
            charge = ref
        elif pos == total_units - leftover - 1:
            charge = per_pack_unit + residual
        else:
            charge = per_pack_unit
        charged_by_line[line_index] = charged_by_line.get(line_index, Decimal(0)) + charge

    by_line: dict = {}
    for i, l in eligible:
        units_here = int(_line_get(l, "quantity", 0))
        descuento = ref * units_here - charged_by_line.get(i, Decimal(0))
        if descuento != 0:
            by_line[i] = descuento
    return by_line


def presentation_package_discount_for_lines(
    db: Session,
    lines: list,
    now: datetime,
    eligible_indices: set | None = None,
) -> PresentationDiscountResult:
    """Descuento de paquete por presentación (research.md D5), con desglose por
    línea. `eligible_indices` (si se pasa) restringe qué líneas pueden aportar
    unidades — lo usa la reconciliación de `combined_discount_detailed` (D6)."""
    result = PresentationDiscountResult()
    promos = active_presentation_promotions(db, now)
    if not promos:
        return result

    raw_total = Decimal(0)
    for promo in promos:
        for rule in promo.presentation_rules:
            eligible = [
                (i, l) for i, l in enumerate(lines)
                if _line_get(l, "combo_id") is None
                and _line_get(l, "presentation_id") == rule.presentation_id
                and _line_get(l, "_variant_active", True)  # FR-015
                and (eligible_indices is None or i in eligible_indices)
            ]
            for i, descuento in _rule_discount_by_line(rule, eligible).items():
                result.by_line[i] = result.by_line.get(i, Decimal(0)) + descuento
                raw_total += descuento
                result.promotion_ids.add(promo.id)

    result.total = raw_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return result


def _line_by_line_discounts(
    db: Session, promo_lines: list, now: datetime,
) -> dict:
    """`{line_index: (monto, promotion_id)}` del motor línea-por-línea
    (`percent`/`fixed`/`qty_price` de producto/categoría) para las líneas SIN
    combo. Misma selección que `evaluate_detailed`, indexada sobre `promo_lines`."""
    valid = active_discount_promotions(db, now)
    out: dict = {}
    if not valid:
        return out
    for i, l in enumerate(promo_lines):
        if _line_get(l, "combo_id") is not None:
            continue
        amount, promo, _ = _best_line_match(
            valid, _line_get(l, "product_id"), _line_get(l, "category_id"),
            int(_line_get(l, "quantity", 0)), Decimal(_line_get(l, "line_total", 0)),
            _line_get(l, "unit_price"),
        )
        if amount > 0 and promo is not None:
            out[i] = (amount, promo.id)
    return out


def combined_discount_detailed(
    db: Session, promo_lines: list, now: datetime,
) -> CombinedDiscountResult:
    """Orquesta los tres mecanismos de descuento y los reconcilia por línea con
    recálculo del pool hasta punto fijo (research.md D6):

    1. `_line_by_line_discounts` — percent/fixed/qty_price de producto/categoría.
    2. `combo_discount_for_lines` — bundles seleccionados (no compiten).
    3. `presentation_package_discount_for_lines` — paquete por presentación, sobre
       el `pool` de líneas elegibles.

    Una línea sale del `pool` si el motor línea-por-línea la deja con total
    **estrictamente menor** (empate → se queda) o si el descuento por presentación
    la dejaría peor que sin promoción (FR-023). Ninguna línea acumula dos
    descuentos (FR-013). Sin ninguna promoción `qty_price_presentation` activa,
    el total coincide **exacto** con `evaluate + combo_discount_for_lines`
    (aditividad-segura)."""
    line_by_line = _line_by_line_discounts(db, promo_lines, now)
    combo_discount = combo_discount_for_lines(db, promo_lines, now)
    combo_ids = {
        _line_get(l, "combo_id") for l in promo_lines
        if _line_get(l, "combo_id") is not None
    }

    pool = {
        i for i, l in enumerate(promo_lines)
        if _line_get(l, "combo_id") is None
        and _line_get(l, "presentation_id") is not None
    }
    pres = presentation_package_discount_for_lines(db, promo_lines, now, pool)
    while pool:
        salen = set()
        for i in list(pool):
            pres_amount = pres.by_line.get(i, Decimal(0))
            if pres_amount <= 0:
                # FR-023: si no la beneficia (o la empeora), sale del pool.
                if pres_amount < 0:
                    salen.add(i)
                continue
            lbl = line_by_line.get(i)
            if lbl is not None and lbl[0] > pres_amount:
                # El motor línea-por-línea deja MENOR total para esa línea.
                salen.add(i)
        if not salen:
            break
        pool -= salen
        pres = presentation_package_discount_for_lines(db, promo_lines, now, pool)

    # Totales: presentación sobre las líneas que quedaron en el pool con
    # descuento positivo; línea-por-línea sobre el resto.
    raw_total = Decimal(0)
    used_ids: set = set()
    for i, l in enumerate(promo_lines):
        if _line_get(l, "combo_id") is not None:
            continue
        pres_amount = pres.by_line.get(i, Decimal(0)) if i in pool else Decimal(0)
        if pres_amount > 0:
            raw_total += pres_amount
            used_ids |= pres.promotion_ids
        elif i in line_by_line:
            raw_total += line_by_line[i][0]
            used_ids.add(line_by_line[i][1])

    total = (raw_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
             + combo_discount)

    if len(combo_ids) == 1:
        promotion_id = next(iter(combo_ids))
    else:
        promotion_id = next(iter(used_ids)) if len(used_ids) == 1 and not combo_ids else None

    return CombinedDiscountResult(total=total, promotion_id=promotion_id)


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
        and _times_overlap(promo, c)
        and _scope_overlap(db, promo, c)
    ]


# --------------------------- CRUD ---------------------------

def list_query(search: str | None = None, status_filter: str | None = None) -> Select:
    """Select filtrado/ordenado para `GET /promotions`. Orden por prioridad
    descendente y luego nombre: el admin ve primero la que gana."""
    stmt = (
        select(Promotion)
        .options(
            selectinload(Promotion.targets),
            selectinload(Promotion.combo_items),
            selectinload(Promotion.presentation_rules).selectinload(
                PromotionPresentationRule.presentation
            ),
        )
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
            promotion_id=promo.id, product_id=t.product_id, category_id=t.category_id,
            value=t.value, min_qty=t.min_qty,
        ))


def _apply_combo_items(db: Session, promo: Promotion, items) -> None:
    promo.combo_items.clear()
    db.flush()
    for c in items:
        db.add(PromotionComboItem(
            promotion_id=promo.id, product_variant_id=c.product_variant_id, quantity=c.quantity,
        ))


# --------------------------- Reglas por presentación (spec 040) ---------------------------

QTY_PRICE_PRESENTATION = "qty_price_presentation"


def _validate_presentation_ids(db: Session, rules) -> None:
    """Cada `presentation_id` de una regla debe existir y estar activa (422)."""
    ids = {r.presentation_id for r in rules}
    if not ids:
        return
    rows = db.execute(
        select(Presentation.id).where(Presentation.id.in_(ids), Presentation.active.is_(True))
    ).scalars().all()
    faltan = ids - set(rows)
    if faltan:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Presentación no encontrada o inactiva",
        )


def _apply_presentation_rules(db: Session, promo: Promotion, rules) -> None:
    """Refuerzo en servicio de "no dos reglas para la misma presentación"
    (FR-006 1ª parte), además del validador Pydantic y el `UniqueConstraint`."""
    seen: set = set()
    for r in rules:
        if r.presentation_id in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "No puede haber dos reglas para la misma presentación",
            )
        seen.add(r.presentation_id)
    promo.presentation_rules.clear()
    db.flush()
    for r in rules:
        db.add(PromotionPresentationRule(
            promotion_id=promo.id, presentation_id=r.presentation_id,
            min_qty=r.min_qty, pack_price=Decimal(r.pack_price),
        ))
    db.flush()


def presentation_overlap_conflicts(
    db: Session, promo: Promotion, presentation_ids: set,
) -> list[dict]:
    """FR-006 2ª parte / CL-4: reglas de **otras** promociones
    `qty_price_presentation` **activas** que cubren alguna de esas presentaciones.
    A diferencia de `find_overlaps` (advertencia), esto **bloquea**."""
    if not presentation_ids:
        return []
    rows = db.execute(
        select(Promotion.id, Promotion.name, PromotionPresentationRule.presentation_id)
        .join(PromotionPresentationRule,
              PromotionPresentationRule.promotion_id == Promotion.id)
        .where(
            Promotion.id != promo.id,
            Promotion.type == QTY_PRICE_PRESENTATION,
            Promotion.status == "active",
            PromotionPresentationRule.presentation_id.in_(presentation_ids),
        )
    ).all()
    return [
        {"promotion_id": str(pid), "promotion_name": name, "presentation_id": str(presid)}
        for pid, name, presid in rows
    ]


def _guard_presentation_overlap(db: Session, promo: Promotion, presentation_ids: set) -> None:
    conflicts = presentation_overlap_conflicts(db, promo, presentation_ids)
    if conflicts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "error": (
                    "Otra promoción de precio por presentación activa ya cubre esa "
                    "presentación"
                ),
                "conflicts": conflicts,
            },
        )


def _active_variants_for_presentation(db: Session, presentation_id) -> list:
    """`[(variant, product)]` de las variantes **activas** que referencian la
    presentación — el alcance real de una regla sobre ella (FR-007)."""
    rows = db.execute(
        select(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            ProductVariant.presentation_id == presentation_id,
            ProductVariant.active.is_(True),
        )
    ).all()
    return [(v, p) for v, p in rows]


def _check_presentation_rule_prices(db: Session, rules, data) -> None:
    """FR-017 (uniformidad de precio) y FR-022 (la regla no representa un
    descuento real): verificaciones con confirmación explícita, **solo** al
    guardar la regla (nunca retroactivo, FR-018).

    Para cada regla se reúnen las variantes **activas** que la referencian y sus
    precios vigentes. `reference_unit_price` = el menor (FR-011) — el que se
    cobrará. Sin el flag correspondiente, cualquiera de las dos condiciones
    devuelve **422** con el detalle estructurado.
    """
    confirm_no_uniforme = getattr(data, "confirm_precio_no_uniforme", False)
    confirm_sin_descuento = getattr(data, "confirm_sin_descuento", False)

    for rule in rules:
        variantes = _active_variants_for_presentation(db, rule.presentation_id)
        if not variantes:
            continue
        precios = [Decimal(v.price) for v, _ in variantes]
        reference = min(precios).quantize(Decimal("0.01"))

        if not confirm_no_uniforme and len(set(precios)) > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Los productos de la presentación no tienen el mismo precio",
                    "presentation_id": str(rule.presentation_id),
                    "reference_unit_price": str(reference),
                    "variants": [
                        {
                            "variant_id": str(v.id),
                            "description": f"{p.name} - {v.name}" if p else v.name,
                            "price": str(v.price),
                        }
                        for v, p in variantes
                    ],
                },
            )

        pack_unit_price = (Decimal(rule.pack_price) / rule.min_qty).quantize(Decimal("0.01"))
        if not confirm_sin_descuento and pack_unit_price >= reference:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "El precio de paquete no representa un descuento",
                    "presentation_id": str(rule.presentation_id),
                    "pack_unit_price": str(pack_unit_price),
                    "reference_unit_price": str(reference),
                },
            )


def _validate_shape_presentation_rules(db: Session, promo: Promotion, data) -> None:
    """Validación de forma de las reglas por presentación tras aplicar el cambio
    de forma (mismo patrón que las de `combo` / `qty_price`, contra el tipo ya
    aplicado)."""
    if promo.type == QTY_PRICE_PRESENTATION and not promo.presentation_rules:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Una promoción de precio por presentación necesita al menos una regla",
        )
    if promo.type != QTY_PRICE_PRESENTATION and promo.presentation_rules:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Las reglas por presentación solo aplican a promociones de ese tipo",
        )
    if promo.type == QTY_PRICE_PRESENTATION:
        _check_presentation_rule_prices(db, list(promo.presentation_rules), data)
        _guard_presentation_overlap(
            db, promo, {r.presentation_id for r in promo.presentation_rules}
        )


def create(db: Session, data) -> Promotion:
    promo = Promotion(
        name=data.name, description=data.description, type=data.type.value,
        value=data.value, status=data.status.value, priority=data.priority,
        starts_at=data.starts_at, ends_at=data.ends_at,
        days_of_week=data.days_of_week,
        start_time=data.start_time, end_time=data.end_time, min_qty=data.min_qty,
    )
    db.add(promo)
    db.flush()
    _apply_targets(db, promo, data.targets)
    _apply_combo_items(db, promo, data.combo_items)
    rules = data.presentation_rules or []
    if rules:
        _validate_presentation_ids(db, rules)
        _apply_presentation_rules(db, promo, rules)
        _check_presentation_rule_prices(db, rules, data)
        _guard_presentation_overlap(db, promo, {r.presentation_id for r in rules})
    db.flush()
    return promo


def update(db: Session, promo: Promotion, data) -> Promotion:
    """Campos escalares. `model_fields_set` (no `is not None`): el admin debe
    poder limpiar un campo opcional enviando `null` explícito."""
    provided = data.model_fields_set
    for field_name in ("name", "description", "value", "priority", "starts_at", "ends_at",
                       "days_of_week", "start_time", "end_time", "min_qty"):
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
    if data.presentation_rules is not None:
        _validate_presentation_ids(db, data.presentation_rules)
        _apply_presentation_rules(db, promo, data.presentation_rules)
    elif promo.type != QTY_PRICE_PRESENTATION and promo.presentation_rules:
        # Cambió de tipo saliendo de `qty_price_presentation`: las reglas huérfanas
        # no tienen sentido.
        promo.presentation_rules.clear()
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
    _validate_shape_presentation_rules(db, promo, data)
    # Aquí, y no en el schema, porque `PromotionShapeUpdate` puede cambiar el
    # tipo y los targets a la vez: el tipo que manda es el ya aplicado.
    if promo.type != "qty_price" and any(
        t.value is not None or t.min_qty is not None for t in promo.targets
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "El precio por producto solo aplica a promociones de tipo paquete (qty_price)",
        )
    if promo.type == "qty_price":
        if not promo.targets:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Un paquete necesita al menos un producto o categoría: el precio se define en cada uno",
            )
        if any(t.value is None or t.min_qty is None for t in promo.targets):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Cada producto o categoría del paquete necesita sus unidades y su precio",
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
    if new_status == "active" and promo.type == QTY_PRICE_PRESENTATION:
        # FR-006 (CL-4): una promoción puede crearse en `draft` sin conflicto y
        # activarse cuando otra ya ocupó esa presentación — se revalida aquí.
        if not promo.presentation_rules:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "No se puede activar una promoción de precio por presentación sin reglas",
            )
        _guard_presentation_overlap(
            db, promo, {r.presentation_id for r in promo.presentation_rules}
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
        days_of_week=promo.days_of_week,
        start_time=promo.start_time, end_time=promo.end_time, min_qty=promo.min_qty,
    )
    db.add(copy)
    db.flush()
    for t in promo.targets:
        db.add(PromotionTarget(
            promotion_id=copy.id, product_id=t.product_id, category_id=t.category_id,
            value=t.value, min_qty=t.min_qty,
        ))
    for c in promo.combo_items:
        db.add(PromotionComboItem(
            promotion_id=copy.id, product_variant_id=c.product_variant_id, quantity=c.quantity,
        ))
    # spec 040: la copia nace `draft`; el solape de FR-006 se revalida al
    # activarla, no al duplicar.
    for r in promo.presentation_rules:
        db.add(PromotionPresentationRule(
            promotion_id=copy.id, presentation_id=r.presentation_id,
            min_qty=r.min_qty, pack_price=r.pack_price,
        ))
    db.flush()
    db.refresh(copy)
    return copy
