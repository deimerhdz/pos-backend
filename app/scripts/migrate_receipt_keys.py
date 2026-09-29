"""Migración de datos (spec 088, FR-008): reescribe a su **key** los comprobantes de pago
(`order_payment_attempts.receipt_file_url`) que hoy guardan una URL absoluta del bucket
gestionado (`pub-…r2.dev/{key}` o `assets.skeilopos.com/{key}`).

    python -m app.scripts.migrate_receipt_keys                     # SIMULACIÓN (por defecto): informa, no modifica
    python -m app.scripts.migrate_receipt_keys --apply             # aplica
    python -m app.scripts.migrate_receipt_keys --apply --tenant acme
    python -m app.scripts.migrate_receipt_keys --revert --apply    # reconstruye {R2_PUBLIC_BASE_URL}/{key}

Espejo operativo de `migrate_image_keys_relative.py` (spec 080): recorre `shared.tenants` una vez
(`with_db(None)`) y luego cada schema de tenant (`with_db(schema)`), con `commit` por schema. No
requiere ventana de mantenimiento ni tabla de control (contracts/scripts.md, research D15).

Reglas por fila (`receipt_file_url`):

- `NULL` -> se omite.
- ya es una key (sin `://`) -> sin cambio (idempotencia).
- URL gestionada cuya key cumple `{esquema}/comprobantes/…` **y** el archivo existe en R2 -> se
  reescribe a la key.
- URL gestionada con key fuera de convención -> intacta, reportada "fuera de convención".
- URL gestionada cuyo archivo no existe en R2 -> intacta, reportada "archivo inexistente".
- URL de otro origen -> intacta, reportada "otro origen".
- R2 no responde al verificar -> intacta, reportada "no verificable"; el script continúa.

Ninguna fila intacta hace fallar el script. **Nunca** modifica ni borra un objeto de R2 (solo cambia
cómo la base de datos referencia su ubicación): usa únicamente `head_object`.

Propiedades: **idempotente** (SC-009: una fila ya migrada es una key y no cumple el predicado, así
que una segunda corrida no la toca) y **reversible** (`--revert` reconstruye la URL pública anterior
solo para keys `{esquema}/comprobantes/…` del propio esquema; nunca toca URLs de otro origen).
"""
import argparse
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import settings
from app.core.db import with_db
from app.core.models import Tenant
from app.core.storage import (
    FOLDER_RECEIPTS,
    AssetKeyError,
    StorageUnavailable,
    _managed_bucket_prefixes,
    normalize_asset_ref,
    object_exists,
    validate_asset_key,
)
from app.models.order_payment_attempt import OrderPaymentAttempt

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _is_managed_url(value: str) -> bool:
    lowered = value.lower()
    return any(lowered.startswith(p.lower()) for p in _managed_bucket_prefixes())


@dataclass
class Report:
    a_reescribir: int = 0        # migración: se reescribirían (o se reescribieron con --apply)
    a_revertir: int = 0          # --revert: se reconstruirían a URL
    ya_key: int = 0
    vacias: int = 0
    archivo_inexistente: int = 0
    otro_origen: int = 0
    fuera_de_convencion: int = 0
    no_verificable: int = 0
    intactas: list[str] = field(default_factory=list)   # detalle de lo que quedó sin tocar y por qué

    def add(self, other: "Report") -> None:
        for name in (
            "a_reescribir", "a_revertir", "ya_key", "vacias", "archivo_inexistente",
            "otro_origen", "fuera_de_convencion", "no_verificable",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.intactas.extend(other.intactas)

    def line(self, label: str, *, revert: bool) -> str:
        head = f"{self.a_revertir} a revertir" if revert else f"{self.a_reescribir} a reescribir"
        parts = [head, f"{self.ya_key} ya key" if not revert else f"{self.ya_key} sin cambio"]
        for count, text_ in (
            (self.archivo_inexistente, "archivo inexistente"),
            (self.otro_origen, "otro origen"),
            (self.fuera_de_convencion, "fuera de convención"),
            (self.no_verificable, "no verificable"),
        ):
            if count:
                parts.append(f"{count} {text_}")
        return f"{label}: " + " · ".join(parts)


def _migrate_value(value: str, schema: str, report: Report, attempt_id) -> str | None:
    """Valor nuevo para una fila en modo migración, o `None` si queda intacta."""
    if not _is_managed_url(value):
        if "://" in value:
            report.otro_origen += 1
            report.intactas.append(f"{schema}  intento={attempt_id}  otro origen  {value}")
        else:
            report.ya_key += 1
        return None

    key = normalize_asset_ref(value)
    try:
        validate_asset_key(key, schema, FOLDER_RECEIPTS)
    except AssetKeyError:
        report.fuera_de_convencion += 1
        report.intactas.append(f"{schema}  intento={attempt_id}  fuera de convención  {value}")
        return None
    try:
        exists = object_exists(key)
    except StorageUnavailable:
        report.no_verificable += 1
        report.intactas.append(f"{schema}  intento={attempt_id}  no verificable  {value}")
        return None
    if not exists:
        report.archivo_inexistente += 1
        report.intactas.append(f"{schema}  intento={attempt_id}  archivo inexistente  {value}")
        return None
    report.a_reescribir += 1
    return key


def _revert_value(value: str, schema: str, report: Report, attempt_id) -> str | None:
    """Valor nuevo para una fila en modo `--revert`, o `None` si queda intacta. Solo una key
    `{esquema}/comprobantes/…` del propio esquema se reconstruye; nunca una URL de otro origen."""
    if "://" not in value and value.startswith(f"{schema}/{FOLDER_RECEIPTS}/"):
        report.a_revertir += 1
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{value}"
    if "://" in value and not _is_managed_url(value):
        report.otro_origen += 1
        report.intactas.append(f"{schema}  intento={attempt_id}  otro origen  {value}")
    else:
        report.ya_key += 1
    return None


def _process_tenant(schema: str, *, write: bool, revert: bool) -> Report:
    report = Report()
    with with_db(schema) as db:
        attempts = db.execute(
            select(OrderPaymentAttempt).where(OrderPaymentAttempt.receipt_file_url.is_not(None))
        ).scalars().all()
        for attempt in attempts:
            value = attempt.receipt_file_url
            if not value:
                report.vacias += 1
                continue
            new_value = (
                _revert_value(value, schema, report, attempt.id) if revert
                else _migrate_value(value, schema, report, attempt.id)
            )
            if write and new_value is not None and new_value != value:
                attempt.receipt_file_url = new_value
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


def run(*, write: bool, revert: bool, tenant: str | None = None) -> int:
    """Devuelve el código de salida: 0 si todo ok (aunque haya filas intactas), 1 si algún
    esquema falló (se loguea y se continúa con los demás)."""
    if revert:
        mode = "REVERSIÓN" if write else "SIMULACIÓN DE LA REVERSIÓN (usa --apply para escribir)"
    else:
        mode = "MIGRACIÓN" if write else "MODO SIMULACIÓN (usa --apply para escribir)"
    print(f"Migración de comprobantes — {mode}\n")

    schemas = _tenant_schemas(tenant)
    if tenant and not schemas:
        logger.error("El negocio '%s' no existe.", tenant)
        return 1

    failed = 0
    total = Report()
    for schema in schemas:
        try:
            report = _process_tenant(schema, write=write, revert=revert)
        except Exception:
            failed += 1
            logger.exception("tenant=%s: falló el procesamiento (se continúa)", schema)
            continue
        print(report.line(schema, revert=revert))
        total.add(report)

    print()
    print(total.line("Total", revert=revert))
    if total.intactas:
        print(f"\n{len(total.intactas)} fila(s) quedaron intactas (revisión manual):")
        for detail in total.intactas:
            print(f"  {detail}")
    if failed:
        logger.warning("%d tenant(s) fallaron — revisa los logs y relanza (idempotente).", failed)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra a key los comprobantes de pago (spec 088). Sin argumentos: simulación.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Ejecuta los UPDATE (commit por schema). Sin este argumento solo se simula.",
    )
    parser.add_argument(
        "--revert", action="store_true",
        help="Invierte la transformación: reconstruye {R2_PUBLIC_BASE_URL}/{key} (con --apply la ejecuta).",
    )
    parser.add_argument("--tenant", metavar="SCHEMA", help="Acota a un negocio.")
    args = parser.parse_args()
    return run(write=args.apply, revert=args.revert, tenant=args.tenant)


if __name__ == "__main__":
    raise SystemExit(main())
