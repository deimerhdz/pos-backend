"""Migración de datos: lleva los `payment_methods` existentes de cada tenant al
catálogo del Super Admin (spec 032, FR-015/FR-015a).

    python -m app.scripts.migrate_payment_methods_catalog --report-only
    python -m app.scripts.migrate_payment_methods_catalog

En dos pasos, a propósito (research.md Decisión 3 y 7; data-model.md
§Migración):

1. `--report-only` (u omitir el flag: reporta igual antes de tocar nada)
   recorre cada tenant, normaliza el `name` de sus `payment_methods` (minúsculas,
   sin tildes) y lo compara contra el catálogo (`130642d23e76_seed_...` ya debe
   estar aplicada). Imprime, por tenant, qué filas matchean y cuáles no — para
   que el Super Admin revise y agregue al catálogo (vía
   `POST /super-admin/payment-methods-catalog`, US1) los métodos personalizados
   que sean válidos, **antes** de correr el backfill (FR-015a).
2. Sin `--report-only`: además de reportar, escribe. Para cada fila que
   matchea, setea `catalog_id` y recalcula `is_complete` contra `catalog.fields`
   vigente — reutilizando `_validate_payment_info`, la misma función que usa
   `sales/service.py` para altas/ediciones nuevas, así que el criterio de
   completitud es idéntico. Reejecutable: una fila que ya tiene `catalog_id` no
   se toca de nuevo.

Nunca borra ni crea filas de `tenant.payment_methods` — solo lee `payment_info`
ya capturado y pobla las dos columnas nuevas (FR-015: "sin requerir que el
tenant vuelva a capturarla").
"""
import argparse
import logging
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select, text

from app.core.db import with_db
from app.api.v1.sales.service import _validate_payment_info
from app.models.payment import PaymentMethod
from app.models.payment_method_catalog import PaymentMethodCatalog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """minúsculas, sin tildes — 'Nequí' y 'nequi' matchean el mismo catálogo."""
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return stripped.strip().lower()


@dataclass
class TenantReport:
    schema: str
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    already_migrated: list[str] = field(default_factory=list)


def _tenant_schemas() -> list[str]:
    with with_db(None) as db:
        return [r[0] for r in db.execute(text("SELECT schema FROM shared.tenants")).fetchall()]


def _catalog_by_normalized_name() -> dict[str, PaymentMethodCatalog]:
    with with_db(None) as db:
        entries = db.execute(select(PaymentMethodCatalog)).scalars().all()
        # Se leen fuera de la sesión (que se cierra al salir del `with`), así
        # que se copian los campos que hacen falta a un dict plano — nada de
        # instancias ORM "colgando" de una sesión ya cerrada.
        return {
            _normalize(e.name): {"id": e.id, "fields": e.fields}
            for e in entries
        }


def _process_tenant(schema: str, catalog_by_name: dict, *, write: bool) -> TenantReport:
    report = TenantReport(schema=schema)
    with with_db(schema) as db:
        methods = db.execute(select(PaymentMethod)).scalars().all()
        for method in methods:
            if method.catalog_id is not None:
                report.already_migrated.append(method.name)
                continue

            catalog_entry = catalog_by_name.get(_normalize(method.name))
            if catalog_entry is None:
                report.unmatched.append(method.name)
                continue

            report.matched.append(method.name)
            if write:
                method.catalog_id = catalog_entry["id"]
                method.is_complete = _validate_payment_info(
                    catalog_entry["fields"], method.payment_info
                )
        if write:
            db.commit()
    return report


def run(*, write: bool) -> list[TenantReport]:
    catalog_by_name = _catalog_by_normalized_name()
    if not catalog_by_name:
        raise RuntimeError(
            "El catálogo está vacío — aplica la migración "
            "130642d23e76_seed_payment_method_catalog antes de ejecutar este script."
        )

    reports = [
        _process_tenant(schema, catalog_by_name, write=write) for schema in _tenant_schemas()
    ]

    for r in reports:
        logger.info(
            "tenant=%s matched=%d unmatched=%s already_migrated=%d",
            r.schema, len(r.matched), r.unmatched or "[]", len(r.already_migrated),
        )
    total_unmatched = sum(len(r.unmatched) for r in reports)
    if total_unmatched:
        logger.warning(
            "%d método(s) de pago sin equivalente en el catálogo — revísalos y agrégalos vía "
            "POST /super-admin/payment-methods-catalog antes de reejecutar (FR-015a).",
            total_unmatched,
        )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra los payment_methods existentes de cada tenant al catálogo (spec 032).",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Solo reporta qué matchea/no matchea; no escribe nada.",
    )
    args = parser.parse_args()

    reports = run(write=not args.report_only)
    return 1 if any(r.unmatched for r in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
