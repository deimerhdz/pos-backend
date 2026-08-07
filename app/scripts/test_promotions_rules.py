"""Reglas del motor de promociones que cuestan dinero si se rompen.

    python -m app.scripts.test_promotions_rules

**No toca la base de datos.** A diferencia del resto de `app/scripts/test_*`,
que trabajan sobre un tenant real, esto ejercita funciones puras
(`_valid_now`, `_in_time_window`, `_line_discount`, `best_line_discount`) con
objetos `Promotion` sin sesión. Por eso puede correr en CI, que es donde debería
estar corriendo antes de cada deploy.

Cubre lo que no tenía ninguna prueba:

  1. La vigencia se evalúa en **hora local**, no en UTC. Antes, en UTC-5, un
     "20% los martes" arrancaba el lunes a las 19:00 locales.
  2. Ventana horaria que **cruza medianoche** (22:00-02:00). Antes era
     insatisfacible: la promo se veía activa y descontaba cero para siempre.
  3. `qty_price` descuenta por **paquetes completos** y deja el remanente a
     precio normal.
  4. `priority` decide el conflicto; el descuento mayor solo desempata.
  5. Las promociones **no se acumulan**: una sola gana por línea.
  6. Ninguna línea queda en negativo.
"""
import os
from datetime import datetime, time, timezone
from decimal import Decimal

# Sin .env real: `Settings` exige credenciales que este script no usa. Se
# rellenan con valores inertes para que el import no falle en CI.
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
    _in_time_window, _line_discount, _valid_now, best_line_discount,
)
from app.models.promotion import Promotion  # noqa: E402

fallos: list[str] = []


def check(nombre: str, ok: bool) -> None:
    print(f"  {'OK  ' if ok else 'FALLA'} {nombre}")
    if not ok:
        fallos.append(nombre)


def promo(**kw) -> Promotion:
    base = dict(
        name=kw.pop("name", "p"), type="percent", value=Decimal("10"),
        status="active", priority=0, min_qty=1,
    )
    base.update(kw)
    p = Promotion(**base)
    # `created_at` es server_default; sin sesión hay que ponerlo a mano porque
    # es el tercer criterio de desempate.
    p.created_at = kw.get("created_at", datetime(2026, 1, 1))
    return p


# --- 1. Hora local, no UTC -------------------------------------------------
print("\n1. Vigencia en hora local del tenant")

# Martes 4 de agosto de 2026, 20:00 en Bogotá = miércoles 5, 01:00 UTC.
utc_miercoles = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)
martes = promo(days_of_week="1")  # 0=lunes..6=domingo -> 1 = martes
check("martes 20:00 local sigue siendo martes", _valid_now(martes, utc_miercoles))

miercoles = promo(days_of_week="2")
check("no se adelanta al miércoles", not _valid_now(miercoles, utc_miercoles))

# Happy hour 15:00-17:00 local = 20:00-22:00 UTC.
hh = promo(start_time=time(15, 0), end_time=time(17, 0))
check("happy hour 15-17 aplica a las 16:00 locales",
      _valid_now(hh, datetime(2026, 8, 4, 21, 0, tzinfo=timezone.utc)))
check("happy hour 15-17 no aplica a las 10:00 locales",
      not _valid_now(hh, datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)))

# Quincena: el día del mes también se lee en local.
quincena = promo(days_of_month="15")
check("quincena: el 15 a las 20:00 locales sigue siendo el 15",
      _valid_now(quincena, datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)))


# --- 2. Ventana que cruza medianoche ---------------------------------------
print("\n2. Ventana horaria con cruce de medianoche")
check("23:00 cae dentro de 22:00-02:00", _in_time_window(time(23, 0), time(22, 0), time(2, 0)))
check("01:00 cae dentro de 22:00-02:00", _in_time_window(time(1, 0), time(22, 0), time(2, 0)))
check("15:00 queda fuera de 22:00-02:00", not _in_time_window(time(15, 0), time(22, 0), time(2, 0)))
check("ventana normal sigue funcionando", _in_time_window(time(16, 0), time(15, 0), time(17, 0)))
check("sin ventana, siempre dentro", _in_time_window(time(3, 0), None, None))


# --- 3. qty_price ----------------------------------------------------------
print("\n3. qty_price: 2 granizados por 8000 (precio normal 5000 c/u)")
pack = promo(type="qty_price", value=Decimal("8000"), min_qty=2)

d = _line_discount(pack, Decimal("10000"), 2, Decimal("5000"))
check("2 unidades descuentan 2000", d == Decimal("2000"))

d = _line_discount(pack, Decimal("15000"), 3, Decimal("5000"))
check("3 unidades: un paquete + remanente a precio normal", d == Decimal("2000"))

d = _line_discount(pack, Decimal("20000"), 4, Decimal("5000"))
check("4 unidades: dos paquetes", d == Decimal("4000"))

d = _line_discount(pack, Decimal("5000"), 1, Decimal("5000"))
check("1 unidad no arma paquete: descuento 0", d == Decimal("0"))

caro = promo(type="qty_price", value=Decimal("99000"), min_qty=2)
check("un paquete más caro que sus partes nunca encarece",
      _line_discount(caro, Decimal("10000"), 2, Decimal("5000")) == Decimal("0"))


# --- 4. Prioridad ----------------------------------------------------------
print("\n4. La prioridad decide, el descuento solo desempata")
baja_pero_grande = promo(name="30% global", value=Decimal("30"), priority=0)
alta_pero_chica = promo(name="5% dirigida", value=Decimal("5"), priority=10)

monto, ganador = best_line_discount(
    [baja_pero_grande, alta_pero_chica], None, None, 1, Decimal("10000")
)
check("gana la de mayor prioridad aunque descuente menos", ganador == alta_pero_chica.id)
check("y descuenta lo suyo, no lo de la otra", monto == Decimal("500"))

a = promo(name="a", value=Decimal("10"), priority=5)
b = promo(name="b", value=Decimal("20"), priority=5)
monto, ganador = best_line_discount([a, b], None, None, 1, Decimal("10000"))
check("a igual prioridad gana el descuento mayor", ganador == b.id)


# --- 5. No acumulación -----------------------------------------------------
print("\n5. Una sola promoción por línea")
p1 = promo(name="p1", value=Decimal("10"))
p2 = promo(name="p2", value=Decimal("20"))
monto, _ = best_line_discount([p1, p2], None, None, 1, Decimal("10000"))
check("10% + 20% no suman 30%", monto == Decimal("2000"))


# --- 6. Nunca negativo -----------------------------------------------------
print("\n6. Ninguna línea queda en negativo")
fijo = promo(type="fixed", value=Decimal("50000"))
check("un fijo mayor que la línea se topa en el total de la línea",
      _line_discount(fijo, Decimal("3000"), 1, Decimal("3000")) == Decimal("3000"))

cien = promo(value=Decimal("100"))
check("100% descuenta exactamente la línea",
      _line_discount(cien, Decimal("3000"), 1, Decimal("3000")) == Decimal("3000"))


# --- 7. min_qty ------------------------------------------------------------
print("\n7. min_qty se mide por línea")
tres = promo(value=Decimal("10"), min_qty=3)
monto, _ = best_line_discount([tres], None, None, 2, Decimal("10000"))
check("2 unidades no alcanzan min_qty=3", monto == Decimal("0"))
monto, _ = best_line_discount([tres], None, None, 3, Decimal("15000"))
check("3 unidades sí", monto == Decimal("1500"))


# --- 8. Estados ------------------------------------------------------------
print("\n8. Solo 'active' descuenta")
ahora = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
for estado in ("draft", "paused", "finished"):
    check(f"estado '{estado}' no aplica", not _valid_now(promo(status=estado), ahora))
check("estado 'active' sí aplica", _valid_now(promo(status="active"), ahora))


print("\n" + "=" * 60)
if fallos:
    print(f"FALLARON {len(fallos)} comprobación(es):")
    for f in fallos:
        print(f"  - {f}")
    raise SystemExit(1)
print("Todas las comprobaciones pasaron.")
