"""Reglas del motor de promociones que cuestan dinero si se rompen.

    python -m app.scripts.test_promotions_rules

**No toca la base de datos.** A diferencia del resto de `app/scripts/test_*`,
que trabajan sobre un tenant real, esto ejercita **funciones puras** del motor
por conjunto de variantes de la spec 063 (`_valid_now`, `_in_time_window`,
`_greedy_units`, `_distribute_group_discount`, `variant_set_condition_text`) con
objetos sin sesión. Por eso puede correr en CI, antes de cada deploy.

spec 063-promociones-por-variante (decisión de negocio A-58…A-65,
registro-de-anomalias.md). Reescritura completa: se fueron `priority`,
`_matching_target` y `_line_discount` de `qty_price`/`fixed`
(contracts/migracion.md §2.5). Cubre:

  1. Vigencia en **hora local**, no en UTC (A-08) + cruce de medianoche +
     atribución de día al cruzar medianoche (**A-57 conservado**).
  2. **Consumo codicioso descendente** de precio: el grupo toma las unidades más
     caras (FR-008); grupos completos + remanente a precio normal (FR-007).
  3. `package_price`: descuento del grupo = `Σ normal − value`, topado en 0
     (FR-006, FR-009). `percent`: `round(value% × Σ normal)` a peso (FR-006).
  4. `_distribute_group_discount` con **división no exacta**: residuo a la
     variante de id más alto; `Σ descuentos por línea == descuento del grupo`,
     al peso, en cualquier orden (FR-008a, SC-005).
  5. Un grupo **nunca encarece** (FR-009).
  6. El `type` **de entrada** admite exactamente `{percent, package_price}`.
"""
import os
from datetime import datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from uuid import UUID

# Sin .env real: `Settings` exige credenciales que este script no usa.
for _k, _v in {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost/x",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET": "test",
    "EMAIL_API_URL": "https://example.invalid",
    "MAIL_FROM_NAME": "t", "MAIL_FROM": "t@example.invalid",
    "R2_ACCOUNT_ID": "x", "R2_ACCESS_KEY_ID": "x", "R2_SECRET_ACCESS_KEY": "x",
    "R2_BUCKET_NAME": "x", "R2_ENDPOINT_URL": "https://example.invalid",
    "R2_PUBLIC_BASE_URL": "https://example.invalid",
    "SUPER_ADMIN_NAME": "t", "SUPER_ADMIN_EMAIL": "t@example.invalid",
    "SUPER_ADMIN_PASSWORD": "t",
}.items():
    os.environ.setdefault(_k, _v)

from app.api.v1.promotions.service import (  # noqa: E402
    _distribute_group_discount, _greedy_units, _in_time_window, _valid_now,
    variant_set_condition_text,
)
from app.api.v1.promotions.schemas import PromotionType  # noqa: E402
from app.models.promotion import PROMOTION_TYPES, Promotion  # noqa: E402

fallos: list[str] = []


def check(nombre: str, ok: bool) -> None:
    print(f"  {'OK  ' if ok else 'FALLA'} {nombre}")
    if not ok:
        fallos.append(nombre)


def promo(**kw) -> Promotion:
    base = dict(
        name=kw.pop("name", "p"), type="percent", value=Decimal("10"),
        status="active", min_qty=1,
    )
    base.update(kw)
    p = Promotion(**base)
    p.created_at = kw.get("created_at", datetime(2026, 1, 1))
    return p


def unit(line_index, price, pv_id, line_id=None):
    return (line_index, Decimal(str(price)), pv_id, line_id or pv_id)


def descuento_grupo(tipo, value, block):
    """Descuento de un grupo completo (research.md D5.d)."""
    normal_g = sum((u[1] for u in block), Decimal(0))
    if tipo == "package_price":
        return max(Decimal(0), normal_g - Decimal(str(value)))
    return (normal_g * Decimal(str(value)) / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )


# --- 1. Hora local + cruce de medianoche + A-57 ---------------------------
print("\n1. Vigencia en hora local del tenant (A-08) y cruce de medianoche (A-57)")

utc_miercoles = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)  # martes 20:00 Bogotá
check("martes 20:00 local sigue siendo martes",
      _valid_now(promo(days_of_week="1"), utc_miercoles))
check("no se adelanta al miércoles",
      not _valid_now(promo(days_of_week="2"), utc_miercoles))

hh = promo(start_time=time(15, 0), end_time=time(17, 0))
check("happy hour 15-17 aplica a las 16:00 locales",
      _valid_now(hh, datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)))
check("happy hour 15-17 no aplica a las 10:00 locales",
      not _valid_now(hh, datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)))

check("23:00 cae dentro de 22:00-02:00", _in_time_window(time(23, 0), time(22, 0), time(2, 0)))
check("01:00 cae dentro de 22:00-02:00", _in_time_window(time(1, 0), time(22, 0), time(2, 0)))
check("15:00 queda fuera de 22:00-02:00", not _in_time_window(time(15, 0), time(22, 0), time(2, 0)))
check("sin ventana, siempre dentro", _in_time_window(time(3, 0), None, None))

# A-57: con la ventana cruzando la medianoche, las horas posteriores a las 00:00
# pertenecen al DÍA DE INICIO para evaluar `days_of_week`.
lunes_noche = promo(days_of_week="0", start_time=time(22, 0), end_time=time(2, 0))
check("lunes 23:00 local: vigente",
      _valid_now(lunes_noche, datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)))
check("martes 01:00 local: sigue siendo el lunes de inicio, vigente",
      _valid_now(lunes_noche, datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)))
check("martes 03:00 local: fuera de ventana, no vigente",
      not _valid_now(lunes_noche, datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)))
check("miércoles 01:00 local: el día de inicio sería el martes, no vigente",
      not _valid_now(lunes_noche, datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)))


# --- 2. Consumo codicioso descendente + grupos completos -----------------
print("\n2. Consumo codicioso: el grupo toma las unidades más caras (FR-008)")

V1, V2, V3 = UUID(int=1), UUID(int=2), UUID(int=3)
unidades = [unit(0, 11000, V1), unit(0, 11000, V1), unit(1, 8000, V2), unit(1, 8000, V2)]
grupos = _greedy_units(unidades, 3)
check("4 unidades, min_qty 3 -> 1 grupo", len(grupos) == 1)
precios_grupo = sorted((u[1] for u in grupos[0]), reverse=True)
check("el grupo toma 11.000 + 11.000 + 8.000 (las 3 más caras)",
      precios_grupo == [Decimal("11000"), Decimal("11000"), Decimal("8000")])

check("2 unidades, min_qty 3 -> 0 grupos (remanente a precio normal, FR-007)",
      _greedy_units([unit(0, 8000, V1), unit(0, 8000, V1)], 3) == [])

seis = [unit(i, 8000, UUID(int=i + 1)) for i in range(6)]
check("6 unidades, min_qty 2 -> 3 grupos de 2", len(_greedy_units(seis, 2)) == 3)


# --- 3. Descuento por grupo: package_price y percent ---------------------
print("\n3. Descuento del grupo (FR-006, FR-009)")

pkg_block = [unit(0, 8000, V1), unit(1, 8000, V2)]
check("package_price: Σ16.000 - 12.000 = 4.000",
      descuento_grupo("package_price", 12000, pkg_block) == Decimal("4000"))
check("package_price que iguala o supera el normal -> 0 (nunca encarece)",
      descuento_grupo("package_price", 20000, pkg_block) == Decimal("0"))

pct_block = [unit(0, 11000, V1), unit(0, 11000, V1), unit(1, 8000, V2)]
check("percent 15% de Σ30.000 = 4.500 (redondeado a peso)",
      descuento_grupo("percent", 15, pct_block) == Decimal("4500"))
check("percent 10% de 23.000 = 2.300",
      descuento_grupo("percent", 10, [unit(0, 15000, V1), unit(1, 8000, V2)]) == Decimal("2300"))


# --- 4. Reparto por importe cobrado + división no exacta (FR-008a, SC-005) -
print("\n4. _distribute_group_discount: cuadra al peso, residuo a la variante de id más alto")

# "3 Pequeños sin licor por 16.000": 3 x 6.000, descuento del grupo 2.000.
alta = UUID(int=99)
block = [unit(0, 6000, UUID(int=1)), unit(1, 6000, UUID(int=2)), unit(2, 6000, alta)]
rep = _distribute_group_discount(block, Decimal("2000"))
check("Σ descuentos por línea == 2.000 (al peso)", sum(rep.values()) == Decimal("2000"))
check("la variante de id más alto descuenta menos (666), las otras 667",
      sorted(rep.values()) == [Decimal("666"), Decimal("667"), Decimal("667")]
      and rep[2] == Decimal("666"))

# Mismo grupo, unidades pasadas en otro orden -> el reparto no cambia: la
# variante de id más alto (`alta`, aquí en line_index 2) sigue descontando 666.
block_rev = [unit(2, 6000, alta), unit(1, 6000, UUID(int=2)), unit(0, 6000, UUID(int=1))]
rep_rev = _distribute_group_discount(block_rev, Decimal("2000"))
check("otro orden de las unidades -> mismo total", sum(rep_rev.values()) == Decimal("2000"))
check("otro orden -> la variante de id más alto sigue en 666", rep_rev[2] == Decimal("666"))

# "15% llevando 3 medianos": grupo 2x11.000 + 1x8.000, descuento 4.500.
grp = [unit(0, 11000, V1), unit(0, 11000, V1), unit(1, 8000, V2)]
rep2 = _distribute_group_discount(grp, Decimal("4500"))
check("reparto -3.300 / -1.200", sorted(rep2.values()) == [Decimal("1200"), Decimal("3300")])
check("Σ == 4.500", sum(rep2.values()) == Decimal("4500"))

# 2X8.000 -> 12.000: descuento 4.000, dos líneas de una unidad -> -2.000 c/u.
rep3 = _distribute_group_discount([unit(0, 8000, V1), unit(1, 8000, V2)], Decimal("4000"))
check("2X: -2.000 por línea", sorted(rep3.values()) == [Decimal("2000"), Decimal("2000")])


# --- 5. Textos de condición (español de Colombia) ------------------------
print("\n5. variant_set_condition_text")

def _promo_texto(tipo, value, min_qty, n):
    # `variant_set_condition_text` solo lee `type`/`value`/`min_qty`/`len(variants)`.
    fake = SimpleNamespace(
        type=tipo, value=Decimal(str(value)), min_qty=min_qty,
        variants=[object()] * n,
    )
    return variant_set_condition_text(fake)

check("package_price min_qty>1",
      _promo_texto("package_price", 12000, 2, 8) == "Llevando 2 de estas 8 variantes pagas $12.000")
check("package_price min_qty 1",
      _promo_texto("package_price", 5000, 1, 3) == "Cada una de estas 3 variantes a $5.000")
check("percent min_qty 1",
      _promo_texto("percent", 10, 1, 5) == "10% en estas 5 variantes")
check("percent min_qty>1",
      _promo_texto("percent", 15, 3, 4) == "15% llevando 3 de estas 4 variantes")
check("promoción finished de tipo viejo -> condition_text None",
      _promo_texto("combo", 0, 1, 0) is None)


# --- 6. El enum de ENTRADA admite exactamente {percent, package_price} ---
print("\n6. Tipos vivos")
check("PromotionType de entrada = {percent, package_price}",
      {t.value for t in PromotionType} == {"percent", "package_price"})
check("PROMOTION_TYPES conserva los viejos + package_price (lee las finished)",
      set(PROMOTION_TYPES) == {"percent", "fixed", "combo", "qty_price",
                               "qty_price_presentation", "package_price"})
_type_len = Promotion.__table__.c.type.type.length
check(f"varchar({_type_len}) admite el más largo ({max(PROMOTION_TYPES, key=len)!r})",
      _type_len >= max(len(t) for t in PROMOTION_TYPES))


print("\n" + "=" * 60)
if fallos:
    print(f"FALLARON {len(fallos)} comprobación(es):")
    for f in fallos:
        print(f"  - {f}")
    raise SystemExit(1)
print("Todas las comprobaciones pasaron.")
