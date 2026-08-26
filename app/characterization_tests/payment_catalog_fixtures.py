"""Infraestructura compartida para los characterization tests del catálogo de
métodos de pago (spec 032) — no es código de producción.

Autónomo, como sus hermanos (`cart_fixtures.py`, `orders_fixtures.py`,
`table_sessions_fixtures.py`, research.md spec 032 §1): no importa nada de
ellos. A diferencia de todos ellos, sí necesita una tabla real del esquema
`shared` (`PaymentMethodCatalog`) — no basta con un doble tipo
`make_tenant_double` porque `sales/service.py` consulta ese modelo de verdad
(valida `payment_info` contra `catalog.fields`, lee `catalog.active`/`.type`).
SQLite no tiene esquemas Postgres: se simula adjuntando una segunda base en
memoria como `shared` (`ATTACH DATABASE ':memory:' AS shared`) y creando ahí
la tabla `payment_method_catalog`, dejando `schema_translate_map={"tenant":
None}` igual que el resto de fixtures para el esquema `tenant`.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.characterization_tests.fixtures import (  # noqa: F401
    make_category,
    make_inventory_item,
    make_option,
    make_option_group,
    make_product,
    make_recipe_item,
    make_unit,
    make_variant,
    link_variant_group,
)
from app.core.models import Base
import app.models  # noqa: F401 - registra todas las tablas de negocio en Base.metadata
from app.models.payment import PaymentMethod
from app.models.payment_method_catalog import PaymentMethodCatalog

__all__ = [
    "new_session",
    "make_payment_method_catalog",
    "make_payment_method",
    "make_tenant_double",
    "make_user_double",
]

_TABLE_NAMES = ["payment_methods"]
_SHARED_TABLE_NAMES = ["payment_method_catalog"]

# `PaymentMethodCatalog.fields`/`PaymentMethod.payment_info` son
# `postgresql.JSONB` — sin este shim `create_all()` falla con
# `UnsupportedCompilationError` sobre SQLite (mismo mecanismo que
# `cart_fixtures.py`/`orders_fixtures.py`).
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


def new_session(*, autoflush: bool = True) -> Session:
    """Sesión SQLAlchemy real sobre SQLite en memoria, con `payment_methods`
    (esquema `tenant`, remapeado) y `payment_method_catalog` (esquema `shared`,
    vía `ATTACH DATABASE`). `uq_payment_method_catalog_id` es una restricción
    única normal (no parcial, spec 032 §catalog_id) — SQLite la soporta tal
    cual, sin el shim de `orders_fixtures._PARTIAL_UNIQUE_INDEXES`."""
    tenant_tables = [t for t in Base.metadata.tables.values() if t.name in _TABLE_NAMES]
    shared_tables = [t for t in Base.metadata.tables.values() if t.name in _SHARED_TABLE_NAMES]

    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect().execution_options(schema_translate_map={"tenant": None})
    conn.execute(text("ATTACH DATABASE ':memory:' AS shared"))
    Base.metadata.create_all(bind=conn, tables=tenant_tables + shared_tables)
    conn.commit()
    return Session(bind=conn, autoflush=autoflush)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


def make_payment_method_catalog(db: Session, **kw) -> PaymentMethodCatalog:
    """`kw.setdefault(fields=[])` (sin campos, tipo Efectivo) por defecto; los
    tests de Nequi/Daviplata pasan `fields=[{"key": "celular", "required":
    True, "format": "numeric", "length": 10}, ...]`."""
    kw.setdefault("id", _uid())
    kw.setdefault("name", f"catalogo-{kw['id']}")
    kw.setdefault("type", "other")
    kw.setdefault("active", True)
    kw.setdefault("fields", [])
    obj = PaymentMethodCatalog(**kw)
    db.add(obj)
    db.flush()
    return obj


def make_payment_method(db: Session, **kw) -> PaymentMethod:
    """`kw.setdefault(is_cash=True)` por defecto (mismo default que
    `cart_fixtures.make_payment_method`). `catalog_id`/`is_complete` son
    opcionales — los tests de spec 032 los pasan explícitamente; los de spec
    024 (`test_sales_payment_methods.py`, editado en esta spec) siguen
    creando filas sin `catalog_id` para los casos que aún no cubre el
    catálogo."""
    kw.setdefault("id", _uid())
    kw.setdefault("name", f"metodo-{kw['id']}")
    kw.setdefault("is_cash", True)
    kw.setdefault("type", "cash" if kw["is_cash"] else "other")
    kw.setdefault("active", True)
    kw.setdefault("is_complete", True)
    obj = PaymentMethod(**kw)
    db.add(obj)
    db.flush()
    return obj


def make_tenant_double(*, id: int = 1, invoice_prefix: str = ""):
    from types import SimpleNamespace

    return SimpleNamespace(id=id, invoice_prefix=invoice_prefix)


def make_user_double(*, id: Optional[uuid.UUID] = None, name: str = "Usuario de prueba"):
    from types import SimpleNamespace

    if id is None:
        id = uuid.uuid4()
    return SimpleNamespace(id=id, name=name)
