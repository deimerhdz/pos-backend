"""Emergencia (spec 089, data-model.md §5 nivel 3): pliega los adicionales cobrados una vez por
línea (`cart_items.addons_total`) de vuelta dentro de `unit_price`, la regla histórica "extras por
unidad".

    python -m app.scripts.fold_line_addons                    # SIMULACIÓN (por defecto): informa, no modifica
    python -m app.scripts.fold_line_addons --apply            # aplica
    python -m app.scripts.fold_line_addons --apply --tenant acme

Solo sirve para limpiar **carritos abiertos** (`cart_items`, aún no enviados) cuando hay que
volver a un binario anterior sin la regla nueva. Por cada línea con `addons_total > 0`:

- si `addons_total` es divisible entre `quantity` a 2 decimales -> `unit_price += addons_total /
  quantity`, `addons_total = 0` y sus opciones `per_line = false` (el total de la línea no cambia);
- si no es exacta -> se lista para resolución manual y **no se toca** (dividir con redondeo rompería
  `unit_price × quantity`).

**Nunca** toca `order_items` ni ventas: su inventario ya se descontó con la regla nueva y una
reversa posterior con la regla vieja descuadraría el kardex. Por eso el `downgrade` de Alembic es de
un solo sentido en cuanto exista el primer pedido con adicionales: desde entonces la única reversa
soportada es `QR_ADDONS_PER_LINE=false`.

Recorre `shared.tenants` una vez (`with_db(None)`) y luego cada schema (`with_db(schema)`), con
`commit` por schema; idempotente (una línea ya plegada tiene `addons_total = 0`).
"""
import argparse
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.db import with_db
from app.core.models import Tenant
from app.models.cart_item import CartItem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


@dataclass
class Report:
    plegadas: int = 0                # se plegarían (o se plegaron con --apply)
    no_exactas: int = 0
    sin_adicionales: int = 0
    detalle: list[str] = field(default_factory=list)   # las que quedaron sin tocar y por qué

    def add(self, other: "Report") -> None:
        self.plegadas += other.plegadas
        self.no_exactas += other.no_exactas
        self.sin_adicionales += other.sin_adicionales
        self.detalle.extend(other.detalle)

    def line(self, label: str) -> str:
        parts = [f"{self.plegadas} a plegar", f"{self.sin_adicionales} sin adicionales"]
        if self.no_exactas:
            parts.append(f"{self.no_exactas} no exactas (revisión manual)")
        return f"{label}: " + " · ".join(parts)


def _fold_amount(item: CartItem) -> Decimal | None:
    """Lo que se suma a `unit_price` si la división es exacta a 2 decimales; `None` si no."""
    addons = Decimal(item.addons_total)
    per_unit = (addons / item.quantity).quantize(_CENT, rounding=ROUND_HALF_UP)
    return per_unit if per_unit * item.quantity == addons else None


def _process_tenant(schema: str, *, write: bool) -> Report:
    report = Report()
    with with_db(schema) as db:
        items = db.execute(
            select(CartItem).where(CartItem.addons_total > 0)
        ).scalars().all()
        for item in items:
            per_unit = _fold_amount(item)
            if per_unit is None:
                report.no_exactas += 1
                report.detalle.append(
                    f"{schema}  línea={item.id}  cantidad={item.quantity}  "
                    f"adicionales={item.addons_total}  no divisible a 2 decimales"
                )
                continue
            report.plegadas += 1
            if write:
                item.unit_price = Decimal(item.unit_price) + per_unit
                item.addons_total = Decimal(0)
                for option in item.options:
                    option.per_line = False
        if write:
            db.commit()   # commit por schema (spec 080)
        else:
            db.rollback()
    return report


def _tenant_schemas(only: str | None) -> list[str]:
    with with_db(None) as db:
        schemas = [row[0] for row in db.execute(select(Tenant.schema).order_by(Tenant.schema)).all()]
    if only is None:
        return schemas
    return [only] if only in schemas else []


def run(*, write: bool, tenant: str | None = None) -> int:
    """Código de salida: 0 si todo ok (aunque haya líneas no exactas), 1 si algún schema falló
    (se loguea y se continúa con los demás)."""
    mode = "APLICACIÓN" if write else "MODO SIMULACIÓN (usa --apply para escribir)"
    print(f"Plegado de adicionales por línea en carritos abiertos — {mode}\n")

    schemas = _tenant_schemas(tenant)
    if tenant and not schemas:
        logger.error("El negocio '%s' no existe.", tenant)
        return 1

    failed = 0
    total = Report()
    for schema in schemas:
        try:
            report = _process_tenant(schema, write=write)
        except Exception:
            failed += 1
            logger.exception("tenant=%s: falló el procesamiento (se continúa)", schema)
            continue
        print(report.line(schema))
        total.add(report)

    print()
    print(total.line("Total"))
    if total.detalle:
        print(f"\n{len(total.detalle)} línea(s) quedaron intactas (resolución manual):")
        for detail in total.detalle:
            print(f"  {detail}")
    print("\nNota: los pedidos ya confirmados (order_items) y las ventas NO se tocan.")
    if failed:
        logger.warning("%d tenant(s) fallaron — revisa los logs y relanza (idempotente).", failed)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pliega los adicionales por línea de los carritos abiertos (spec 089). "
                    "Sin argumentos: simulación.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Ejecuta los UPDATE (commit por schema). Sin este argumento solo se simula.",
    )
    parser.add_argument("--tenant", metavar="SCHEMA", help="Acota a un negocio.")
    args = parser.parse_args()
    return run(write=args.apply, tenant=args.tenant)


if __name__ == "__main__":
    raise SystemExit(main())
