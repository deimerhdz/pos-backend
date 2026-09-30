"""089 line addons (adicionales cobrados una vez por línea)

Revision ID: a89f0c1d2e3b
Revises: deb68185a606
Create Date: 2026-09-30 10:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'a89f0c1d2e3b'
down_revision: Union[str, Sequence[str], None] = 'deb68185a606'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


def _has_column(schema: str, table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table, schema=schema)
    }


def _has_check(schema: str, table: str, name: str) -> bool:
    return name in {
        c["name"] for c in sa.inspect(op.get_bind()).get_check_constraints(table, schema=schema)
    }


# (tabla, columna, tipo, default)
_ADDONS_TOTAL = [("cart_items", "ck_cart_item_addons_total_nonneg"),
                 ("order_items", "ck_order_item_addons_total_nonneg")]
_PER_LINE_TABLES = ["cart_item_options", "order_item_options"]


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """spec 089 (A-94, data-model.md §1). Solo metadatos en PostgreSQL 16 (DEFAULT
    constante, sin reescritura de tabla) y **sin backfill**: las filas existentes
    quedan con `addons_total = 0` y `per_line = false`, que reproducen el cálculo
    de siempre (`line_total = unit_price × quantity`). Idempotente."""
    for table, check_name in _ADDONS_TOTAL:
        if not _has_table(schema, table):
            continue
        if not _has_column(schema, table, "addons_total"):
            op.add_column(
                table,
                sa.Column("addons_total", sa.Numeric(12, 2), nullable=False,
                          server_default=sa.text("0")),
                schema=schema,
            )
        if not _has_check(schema, table, op.f(f"ck__{table}__{check_name}")):
            op.create_check_constraint(
                op.f(f"ck__{table}__{check_name}"), table, "addons_total >= 0", schema=schema,
            )
    for table in _PER_LINE_TABLES:
        if not _has_table(schema, table):
            continue
        if not _has_column(schema, table, "per_line"):
            op.add_column(
                table,
                sa.Column("per_line", sa.Boolean(), nullable=False,
                          server_default=sa.text("false")),
                schema=schema,
            )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Elimina las columnas **solo si** ninguna línea usa la regla nueva. Con líneas
    con `addons_total > 0` un binario anterior las subcobraría (ignora la columna):
    la única reversa soportada entonces es `QR_ADDONS_PER_LINE=false` (data-model §5)."""
    for table, _check in _ADDONS_TOTAL:
        if not _has_table(schema, table) or not _has_column(schema, table, "addons_total"):
            continue
        used = op.get_bind().execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}" WHERE addons_total > 0')
        ).scalar()
        if used:
            raise RuntimeError(
                f"No se puede revertir la migración 089: {schema}.{table} tiene {used} línea(s) "
                "con addons_total > 0 y un binario anterior las subcobraría. Apaga la regla "
                "con QR_ADDONS_PER_LINE=false en lugar de hacer downgrade (data-model.md §5)."
            )
    for table, check_name in _ADDONS_TOTAL:
        if not _has_table(schema, table) or not _has_column(schema, table, "addons_total"):
            continue
        if _has_check(schema, table, op.f(f"ck__{table}__{check_name}")):
            op.drop_constraint(op.f(f"ck__{table}__{check_name}"), table, schema=schema, type_="check")
        op.drop_column(table, "addons_total", schema=schema)
    for table in _PER_LINE_TABLES:
        if _has_table(schema, table) and _has_column(schema, table, "per_line"):
            op.drop_column(table, "per_line", schema=schema)
