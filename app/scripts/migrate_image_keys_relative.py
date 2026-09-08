"""Migración de datos (spec 080, FR-009): reescribe a su **key relativa** todas
las filas de `Tenant.logo_url`, `Product.image_url` y los valores de imagen de
`PaymentMethod.payment_info` que hoy guardan una URL absoluta del bucket
gestionado (`pub-…r2.dev/{key}` o `assets.skeilopos.com/{key}`).

    python -m app.scripts.migrate_image_keys_relative --report-only   # cuenta, no escribe
    python -m app.scripts.migrate_image_keys_relative                 # migra
    python -m app.scripts.migrate_image_keys_relative --revert        # reconstruye {R2_PUBLIC_BASE_URL}/{key}

Espejo operativo de `migrate_payment_methods_catalog.py`: recorre
`shared.tenants` una vez (`with_db(None)`) y luego cada schema de tenant
(`with_db(schema)`), con `commit` por schema. No requiere ventana de
mantenimiento.

Propiedades (contracts/data-migration.md):

- **Idempotente / re-ejecutable con la app en marcha** (FR-009c, SC-009): el
  predicado exige "el valor empieza por un prefijo del bucket gestionado"; una
  fila ya migrada (key) ya no lo cumple, así que una segunda corrida —o una que
  se relanza tras interrumpirse— no la toca. No hace falta tabla de control.
- **Reversible** (FR-009a, SC-008): `--revert` reconstruye la URL pública
  anterior exacta a partir de la key. Solo toca valores que son una key de
  **este** tenant (empiezan por `{schema}/`), nunca un `celular`/`cuenta` ni una
  URL de otro origen. Asume que no se subieron imágenes nuevas entre `migrar` y
  `--revert` (que nacerían como key y el revert las dejaría como URL vieja
  apuntando al mismo objeto — la imagen se sigue viendo, solo cambia el dominio).
- **No toca ningún objeto de R2** (FR-009a): solo cambia cómo la base de datos
  referencia la ubicación de un objeto.
- **Nunca toca** `receipt_file_url` / carpeta `comprobantes` (FR-014): no aparece
  en el recorrido.
- **Valores de otro origen** (Supabase u otra fuente) quedan intactos (FR-010).
"""
import argparse
import logging
from dataclasses import dataclass, field

from sqlalchemy import select, text

from app.core.config import settings
from app.core.db import with_db
from app.core.models import Tenant
from app.core.storage import _managed_bucket_prefixes, normalize_asset_ref
from app.models.payment import PaymentMethod
from app.models.product import Product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _is_managed_url(value: str) -> bool:
    lowered = value.lower()
    return any(lowered.startswith(p.lower()) for p in _managed_bucket_prefixes())


@dataclass
class FieldReport:
    label: str
    migradas: int = 0
    revertidas: int = 0
    ya_key: int = 0
    vacias: int = 0
    otro_origen: int = 0

    def line(self, prefix: str) -> str:
        return (
            f"{prefix}{self.label}: migradas={self.migradas} revertidas={self.revertidas} "
            f"ya_key={self.ya_key} vacias={self.vacias} otro_origen={self.otro_origen}"
        )


def _transform_value(value, schema: str, *, revert: bool, report: FieldReport):
    """Devuelve el valor nuevo para una **referencia de imagen escalar** (celda
    de `logo_url` / `image_url`, o un valor string de `payment_info` que ya se
    sabe que es una referencia de imagen). Actualiza `report`."""
    if not value:
        report.vacias += 1
        return value
    if not isinstance(value, str):
        return value

    if revert:
        # solo una key de este tenant se reconstruye a la URL pública anterior
        if "://" not in value and value.startswith(f"{schema}/"):
            report.revertidas += 1
            return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{value}"
        if _is_managed_url(value):
            report.ya_key += 1  # (dominio gestionado sin migrar aún) — no lo toca el revert
            return value
        report.otro_origen += 1
        return value

    # modo migración
    if _is_managed_url(value):
        report.migradas += 1
        return normalize_asset_ref(value)
    if "://" in value:
        report.otro_origen += 1
        return value
    report.ya_key += 1
    return value


def _is_image_ref(value, schema: str) -> bool:
    """Un valor de `payment_info` cuenta como referencia de imagen si es una URL
    (gestionada o de otro origen) o una key de este tenant — nunca un `celular`
    o `cuenta` planos."""
    return isinstance(value, str) and bool(value) and (
        "://" in value or value.startswith(f"{schema}/")
    )


def _transform_payment_info(payment_info, schema: str, *, revert: bool, report: FieldReport):
    if not isinstance(payment_info, dict):
        return payment_info, False
    new_info = {}
    changed = False
    for key, value in payment_info.items():
        if _is_image_ref(value, schema):
            new_value = _transform_value(value, schema, revert=revert, report=report)
            if new_value != value:
                changed = True
            new_info[key] = new_value
        else:
            new_info[key] = value
    return new_info, changed


def _process_shared(*, write: bool, revert: bool) -> FieldReport:
    report = FieldReport(label="shared.tenants.logo_url")
    with with_db(None) as db:
        for tenant in db.execute(select(Tenant)).scalars().all():
            new_value = _transform_value(
                tenant.logo_url, tenant.schema, revert=revert, report=report
            )
            if write and new_value != tenant.logo_url:
                tenant.logo_url = new_value
        if write:
            db.commit()
    return report


def _process_tenant(schema: str, *, write: bool, revert: bool) -> tuple[FieldReport, FieldReport]:
    img = FieldReport(label="image_url")
    pay = FieldReport(label="payment_info")
    with with_db(schema) as db:
        for product in db.execute(select(Product)).scalars().all():
            new_value = _transform_value(product.image_url, schema, revert=revert, report=img)
            if write and new_value != product.image_url:
                product.image_url = new_value

        for method in db.execute(select(PaymentMethod)).scalars().all():
            new_info, changed = _transform_payment_info(
                method.payment_info, schema, revert=revert, report=pay
            )
            if write and changed:
                method.payment_info = new_info

        if write:
            db.commit()
    return img, pay


def _tenant_schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def run(*, write: bool, revert: bool) -> int:
    """Devuelve el código de salida: 0 si todo ok, 1 si algún tenant falló (se
    loguea y se continúa con los demás)."""
    mode = "revert" if revert else ("migración" if write else "report-only")
    logger.info("migrate_image_keys_relative — modo: %s", mode)

    shared_report = _process_shared(write=write, revert=revert)
    logger.info(shared_report.line(""))

    failed = 0
    totals = FieldReport(label="TOTAL")
    for schema in _tenant_schemas():
        try:
            img, pay = _process_tenant(schema, write=write, revert=revert)
        except Exception:
            failed += 1
            logger.exception("tenant=%s: falló el procesamiento (se continúa)", schema)
            continue
        logger.info(img.line(f"tenant={schema}  "))
        logger.info(pay.line(f"tenant={schema}  "))
        for r in (img, pay):
            totals.migradas += r.migradas
            totals.revertidas += r.revertidas
            totals.ya_key += r.ya_key
            totals.vacias += r.vacias
            totals.otro_origen += r.otro_origen

    for attr in ("migradas", "revertidas", "ya_key", "vacias", "otro_origen"):
        setattr(totals, attr, getattr(totals, attr) + getattr(shared_report, attr))
    logger.info(totals.line(""))
    if totals.otro_origen:
        logger.info(
            "%d valor(es) de otro origen se dejaron intactos (FR-010) — revisa que sea esperado.",
            totals.otro_origen,
        )
    if failed:
        logger.warning("%d tenant(s) fallaron — revisa los logs y relanza (idempotente).", failed)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra a key relativa las imágenes de producto / logo / método de pago (spec 080).",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Solo cuenta las filas que se tocarían por tenant y campo; no escribe nada. "
             "Combinable con --revert para un ensayo de la reversión.",
    )
    parser.add_argument(
        "--revert", action="store_true",
        help="Reconstruye {R2_PUBLIC_BASE_URL}/{key} en las filas que hoy son key (SC-008).",
    )
    args = parser.parse_args()
    return run(write=not args.report_only, revert=args.revert)


if __name__ == "__main__":
    raise SystemExit(main())
