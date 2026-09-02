"""orden de categorías en el filtro del menú QR: categories.display_order

Revision ID: 94144eaa60b5
Revises: a96852d7be6a
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '94144eaa60b5'
down_revision: Union[str, Sequence[str], None] = 'a96852d7be6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


def backfill_sql(schema: str) -> str:
    """SQL de backfill (spec 067, FR-009/SC-003, research.md Decisión 4): el
    Menú QR hoy ordena por `Category.name` ASCENDENTE; este feature ordenará
    por `display_order` DESCENDENTE. Para reproducir exactamente la
    secuencia visible hoy, el nombre alfabéticamente más pequeño debe
    recibir el valor más alto -- de ahí `ORDER BY name DESC` en el
    ROW_NUMBER(). Factorizado en su propia función para poder probar la
    fórmula directamente contra SQLite (test_category_display_order.py),
    igual que `migrate_promotions_data` (spec 063)."""
    return f"""
        UPDATE {schema}.categories
        SET display_order = sub.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (ORDER BY name DESC) AS rn
            FROM {schema}.categories
        ) sub
        WHERE {schema}.categories.id = sub.id
    """


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "categories"):
        return

    op.add_column(
        "categories",
        sa.Column("display_order", sa.Integer(), nullable=True),
        schema=schema,
    )

    op.execute(backfill_sql(schema))

    op.alter_column("categories", "display_order", nullable=False, schema=schema)

    op.create_check_constraint(
        "ck_category_display_order_non_negative",
        "categories",
        "display_order >= 0",
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "categories"):
        return
    op.drop_constraint(
        "ck_category_display_order_non_negative",
        "categories",
        schema=schema,
        type_="check",
    )
    op.drop_column("categories", "display_order", schema=schema)
