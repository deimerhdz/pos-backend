"""mesas avanzado: estados de mesa + merged_group_id en customer_orders

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "dining_tables"):
        return

    # dining_tables.status: ampliar a 4 estados.
    op.alter_column("dining_tables", "status", type_=sa.String(15),
                    existing_nullable=False, schema=schema)
    op.drop_constraint(op.f("ck__dining_tables__ck_dining_table_status"), "dining_tables",
                       type_="check", schema=schema)
    op.create_check_constraint(
        "ck_dining_table_status", "dining_tables",
        "status IN ('libre', 'ocupada', 'reservada', 'pendiente_pago')", schema=schema,
    )

    # customer_orders.merged_group_id (unión de mesas).
    op.add_column("customer_orders", sa.Column("merged_group_id", sa.UUID(), nullable=True),
                  schema=schema)
    op.create_index(op.f("ix__customer_orders__merged_group_id"), "customer_orders",
                    ["merged_group_id"], schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "dining_tables"):
        return
    op.drop_index(op.f("ix__customer_orders__merged_group_id"), "customer_orders", schema=schema)
    op.drop_column("customer_orders", "merged_group_id", schema=schema)

    op.execute(f"UPDATE {schema}.dining_tables SET status = 'ocupada' "
               f"WHERE status IN ('reservada', 'pendiente_pago')")
    op.drop_constraint(op.f("ck__dining_tables__ck_dining_table_status"), "dining_tables",
                       type_="check", schema=schema)
    op.create_check_constraint(
        "ck_dining_table_status", "dining_tables",
        "status IN ('libre', 'ocupada')", schema=schema,
    )
    op.alter_column("dining_tables", "status", type_=sa.String(10),
                    existing_nullable=False, schema=schema)
