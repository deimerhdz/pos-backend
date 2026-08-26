"""order item discount snapshot

Revision ID: 187e491e597a
Revises: 205f518df786
Create Date: 2026-08-26 13:15:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '187e491e597a'
down_revision: Union[str, Sequence[str], None] = '205f518df786'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Los esquemas de scratch (tenant_default) pueden no tener las tablas base.
    if not _has_table(schema, "order_items"):
        return

    op.add_column(
        "order_items",
        sa.Column("discounted_unit_price", sa.Numeric(12, 2), nullable=True),
        schema=schema,
    )
    op.add_column(
        "order_items",
        sa.Column("discounted_line_total", sa.Numeric(12, 2), nullable=True),
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "order_items"):
        return

    op.drop_column("order_items", "discounted_line_total", schema=schema)
    op.drop_column("order_items", "discounted_unit_price", schema=schema)
