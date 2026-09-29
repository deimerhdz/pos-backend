"""Reporte de reconciliación entre la base de datos y Cloudflare R2 (spec 088, FR-009).

    python -m app.scripts.reconcile_r2_references
    python -m app.scripts.reconcile_r2_references --tenant acme
    python -m app.scripts.reconcile_r2_references --grace-hours 48 --output /tmp/r2.csv --format csv

**Solo lectura** (contracts/scripts.md §1, research D14): no ejecuta `UPDATE`/`DELETE`/`INSERT`,
nunca hace `commit` y solo llama `list_objects_v2` y `head_object` sobre R2 — este módulo no importa
`delete_object` ni `put_object`. Sirve para que el operador vea qué quedó roto o huérfano; qué hacer
con cada hallazgo (borrar, restaurar, dejar) es una acción manual suya, fuera de alcance.

Reúne las cuatro fuentes de referencias (las mismas que consulta el borrado, `asset_refs`):
`products.image_url`, las claves `format:"image"` de `payment_methods.payment_info`,
`order_payment_attempts.receipt_file_url` (esquema de cada negocio) y `shared.tenants.logo_url`.
Un solo listado paginado del bucket y tres clases de hallazgo por negocio:

- **referencia sin archivo**: referencia gestionada cuya key no existe en R2;
- **archivo sin referencia**: objeto que ninguna referencia menciona y cuyo `LastModified` es
  anterior a la ventana de gracia (una subida reciente puede estar aún sin guardar);
- **fuera de convención**: referencia gestionada cuya key no cumple `{esquema}/{carpeta}/{nombre}`
  para su campo (histórica, o de otro negocio).

Las referencias de otro origen (Supabase u otra fuente) se cuentan aparte y no se verifican (FR-005).

Con `--tenant` solo se leen las tablas de ese negocio (más todos los logos, que son baratos); un
archivo de ese prefijo que solo lo referenciara una fila de *otro* negocio se listaría como huérfano.
"""
import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import with_db
from app.core.models import Tenant
from app.core.storage import (
    FOLDER_LOGO,
    FOLDER_PAYMENT_METHODS,
    FOLDER_PRODUCTS,
    FOLDER_RECEIPTS,
    AssetKeyError,
    get_r2_client,
    normalize_asset_ref,
    validate_asset_key,
)
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.models.payment import PaymentMethod
from app.models.product import Product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TIPO_SIN_ARCHIVO = "referencia_sin_archivo"
TIPO_HUERFANO = "archivo_sin_referencia"
TIPO_FUERA_DE_CONVENCION = "fuera_de_convencion"

SIN_NEGOCIO = "(sin negocio)"


@dataclass(frozen=True)
class Reference:
    negocio: str
    campo: str
    folder: str
    value: str          # tal como está en base de datos
    detalle: str


@dataclass(frozen=True)
class Finding:
    negocio: str
    tipo: str
    campo: str
    key: str
    detalle: str


def _make_readonly(db) -> None:
    """`SET TRANSACTION READ ONLY` en PostgreSQL: la base rechaza cualquier escritura de esta
    sesión aunque el código se equivocara. SQLite (tests) no lo soporta."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))


def _read_shared_tenants() -> list[tuple[str, str | None]]:
    with with_db(None) as db:
        _make_readonly(db)
        rows = db.execute(select(Tenant.schema, Tenant.logo_url).order_by(Tenant.schema)).all()
        db.rollback()
    return [(schema, logo) for schema, logo in rows]


def _read_tenant_references(schema: str) -> list[Reference]:
    refs: list[Reference] = []
    with with_db(schema) as db:
        _make_readonly(db)
        for product in db.execute(select(Product.id, Product.image_url)).all():
            if product.image_url:
                refs.append(Reference(schema, "products.image_url", FOLDER_PRODUCTS,
                                      product.image_url, f"producto {product.id}"))

        methods = db.execute(
            select(PaymentMethod).options(selectinload(PaymentMethod.catalog))
        ).scalars().all()
        for method in methods:
            image_keys = [f["key"] for f in (method.fields or []) if f.get("format") == "image"]
            for key in image_keys:
                value = (method.payment_info or {}).get(key)
                if isinstance(value, str) and value:
                    refs.append(Reference(schema, f"payment_methods.payment_info.{key}",
                                          FOLDER_PAYMENT_METHODS, value, f"método {method.name}"))

        for attempt in db.execute(
            select(OrderPaymentAttempt.id, OrderPaymentAttempt.receipt_file_url)
            .where(OrderPaymentAttempt.receipt_file_url.is_not(None))
        ).all():
            if attempt.receipt_file_url:
                refs.append(Reference(schema, "order_payment_attempts.receipt_file_url", FOLDER_RECEIPTS,
                                      attempt.receipt_file_url, f"intento {attempt.id}"))
        db.rollback()   # solo lectura: nunca commit
    return refs


def _list_bucket(client, prefix: str | None) -> dict[str, datetime]:
    """Un solo listado paginado del bucket: `key -> LastModified`."""
    objects: dict[str, datetime] = {}
    token = None
    while True:
        kwargs = {"Bucket": settings.R2_BUCKET_NAME}
        if prefix:
            kwargs["Prefix"] = prefix
        if token:
            kwargs["ContinuationToken"] = token
        response = client.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            objects[item["Key"]] = item["LastModified"]
        if not response.get("IsTruncated"):
            return objects
        token = response.get("NextContinuationToken")


def _exists(client, key: str) -> bool:
    try:
        client.head_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
        return True
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code", "")) in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise
    except BotoCoreError:
        raise


def reconcile(*, tenant: str | None, grace_hours: int, now: datetime | None = None):
    """Devuelve `(findings, otro_origen, negocios)`; no modifica nada."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=grace_hours)

    tenants = _read_shared_tenants()
    all_schemas = [schema for schema, _ in tenants]
    if tenant and tenant not in all_schemas:
        raise SystemExit(f"El negocio '{tenant}' no existe.")
    negocios = [tenant] if tenant else all_schemas

    references: list[Reference] = []
    for schema, logo in tenants:
        if logo:
            # Los logos son baratos: se leen siempre, también con --tenant, porque un archivo del
            # prefijo acotado puede estar referenciado por el logo de cualquier negocio.
            references.append(Reference(schema, "shared.tenants.logo_url", FOLDER_LOGO, logo, f"negocio {schema}"))
    for schema in negocios:
        references.extend(_read_tenant_references(schema))

    client = get_r2_client()
    prefix = f"{tenant}/" if tenant else None
    objects = _list_bucket(client, prefix)

    findings: list[Finding] = []
    otro_origen = 0
    referenced: set[str] = set()
    for ref in references:
        normalized = normalize_asset_ref(ref.value)
        if normalized is None:
            continue
        if "://" in normalized:
            otro_origen += 1        # FR-005: se cuenta aparte, no se verifica
            continue
        referenced.add(normalized)
        if tenant and ref.campo == "shared.tenants.logo_url" and ref.negocio != tenant:
            continue                # el logo de otro negocio solo cuenta como "referenciado"

        try:
            validate_asset_key(normalized, ref.negocio, ref.folder)
        except AssetKeyError:
            findings.append(Finding(ref.negocio, TIPO_FUERA_DE_CONVENCION, ref.campo, normalized, ref.detalle))

        if normalized in objects:
            continue
        if prefix and not normalized.startswith(prefix) and _exists(client, normalized):
            continue                # fuera del prefijo listado: se confirma con HEAD
        findings.append(Finding(ref.negocio, TIPO_SIN_ARCHIVO, ref.campo, normalized, ref.detalle))

    known = set(all_schemas)
    for key, modified in sorted(objects.items()):
        if key in referenced or modified > cutoff:
            continue
        owner = key.split("/", 1)[0] if "/" in key else SIN_NEGOCIO
        findings.append(Finding(
            owner if owner in known else SIN_NEGOCIO, TIPO_HUERFANO, "", key,
            f"última modificación {modified.strftime('%Y-%m-%d %H:%M')}",
        ))
    return findings, otro_origen, negocios


def _print_report(findings: list[Finding], otro_origen: int, negocios: list[str], grace_hours: int,
                  now: datetime) -> None:
    by_negocio: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_negocio[finding.negocio].append(finding)

    print(f"Reconciliación R2 — {now.strftime('%Y-%m-%d %H:%M')} (ventana de gracia: {grace_hours} h)\n")
    titles = {
        TIPO_SIN_ARCHIVO: "Referencias sin archivo",
        TIPO_HUERFANO: "Archivos sin referencia",
        TIPO_FUERA_DE_CONVENCION: "Fuera de convención",
    }
    for negocio in negocios:
        print(f"Negocio: {negocio}")
        items = by_negocio.get(negocio, [])
        if not items:
            print("  Sin hallazgos.\n")
            continue
        for tipo in (TIPO_SIN_ARCHIVO, TIPO_HUERFANO, TIPO_FUERA_DE_CONVENCION):
            group = [f for f in items if f.tipo == tipo]
            if not group:
                continue
            print(f"  {titles[tipo]} ({len(group)})")
            for f in group:
                if tipo == TIPO_HUERFANO:
                    print(f"    {f.key}   {f.detalle}")
                else:
                    print(f"    {f.campo:<26}{f.key}   ({f.detalle})")
        print()
    sin_negocio = by_negocio.get(SIN_NEGOCIO, [])
    print(f"Sin negocio (archivos fuera de un prefijo conocido): {len(sin_negocio)}")
    for f in sin_negocio:
        print(f"    {f.key}   {f.detalle}")
    print(f"Referencias de otro origen (no verificadas): {otro_origen}\n")
    counts = {tipo: sum(1 for f in findings if f.tipo == tipo)
              for tipo in (TIPO_SIN_ARCHIVO, TIPO_HUERFANO, TIPO_FUERA_DE_CONVENCION)}
    print(f"Resumen: {counts[TIPO_SIN_ARCHIVO]} sin archivo · {counts[TIPO_HUERFANO]} huérfanos · "
          f"{counts[TIPO_FUERA_DE_CONVENCION]} fuera de convención")


def _write_output(findings: list[Finding], path: str, fmt: str) -> None:
    fields = ["negocio", "tipo", "campo", "key", "detalle"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        if fmt == "json":
            json.dump([asdict(f) for f in findings], handle, ensure_ascii=False, indent=2)
        else:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for f in findings:
                writer.writerow(asdict(f))


def run(*, tenant: str | None = None, grace_hours: int = 24, output: str | None = None,
        fmt: str = "csv", now: datetime | None = None) -> int:
    """Código de salida: 0 siempre que el reporte se genere (haya o no hallazgos: es un informe,
    no una validación); distinto de 0 solo por un error operativo (sin base de datos o sin R2)."""
    now = now or datetime.now(timezone.utc)
    try:
        findings, otro_origen, negocios = reconcile(tenant=tenant, grace_hours=grace_hours, now=now)
    except SystemExit:
        raise
    except Exception:
        logger.exception("No se pudo generar el reporte (base de datos o R2 no disponibles)")
        return 1
    _print_report(findings, otro_origen, negocios, grace_hours, now)
    if output:
        _write_output(findings, output, fmt)
        print(f"Archivo escrito: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reporte de solo lectura de referencias rotas, archivos huérfanos y keys fuera de "
                    "convención en R2 (spec 088).",
    )
    parser.add_argument("--tenant", metavar="SCHEMA", help="Acota referencias y objetos a ese negocio.")
    parser.add_argument("--grace-hours", type=int, default=24,
                        help="Un archivo sin referencia más reciente que esta ventana no se lista (24).")
    parser.add_argument("--output", metavar="RUTA", help="Además escribe el resultado a RUTA.")
    parser.add_argument("--format", choices=("csv", "json"), default="csv", dest="fmt",
                        help="Formato de --output (csv por defecto).")
    args = parser.parse_args()
    return run(tenant=args.tenant, grace_hours=args.grace_hours, output=args.output, fmt=args.fmt)


if __name__ == "__main__":
    sys.exit(main())
