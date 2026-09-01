"""option_groups.pricing_type (spec 064: sabor incluido vs. topping con recargo)

Revision ID: 68326ed66ebf
Revises: 94b7e35f5e5e
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = '68326ed66ebf'
down_revision: Union[str, Sequence[str], None] = '94b7e35f5e5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "option_groups"):
        return
    op.add_column(
        "option_groups",
        sa.Column("pricing_type", sa.String(length=20), nullable=False, server_default="con_recargo"),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_option_group_pricing_type",
        "option_groups",
        "pricing_type IN ('incluido', 'con_recargo')",
        schema=schema,
    )
    # Backfill (spec 064, FR-015): un grupo con al menos una opción (activa o no) con
    # extra_price > 0 queda "con_recargo"; si todas están en $0, queda "incluido". Es
    # puramente clasificatorio -- no toca extra_price, inventory_item_id ni
    # item_quantity de ninguna opción existente.
    op.execute(
        text(f"""
            UPDATE {schema}.option_groups og
            SET pricing_type = CASE
                WHEN EXISTS (
                    SELECT 1 FROM {schema}.options o
                    WHERE o.option_group_id = og.id AND o.extra_price > 0
                ) THEN 'con_recargo'
                ELSE 'incluido'
            END
        """)
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "option_groups"):
        return
    op.drop_constraint("ck_option_group_pricing_type", "option_groups", schema=schema)
    op.drop_column("option_groups", "pricing_type", schema=schema)
