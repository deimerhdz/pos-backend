"""087 numeracion pedidos mesa

Revision ID: b605b7c334f2
Revises: c7e2b91a4d35
Create Date: 2026-09-28 17:00:56.185041

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'b605b7c334f2'
down_revision: Union[str, Sequence[str], None] = 'c7e2b91a4d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """spec 087, FR-006 (data-model.md §1): numeración estable de pedidos de
    mesa, persistida en el backend y reiniciada por turno de caja."""
    if not _has_table(schema, "customer_orders"):
        return
    op.add_column("customer_orders", sa.Column("cash_shift_id", sa.UUID(), nullable=True), schema=schema)
    op.add_column("customer_orders", sa.Column("table_order_number", sa.Integer(), nullable=True), schema=schema)
    op.create_foreign_key(
        op.f("fk__customer_orders__cash_shift_id__cash_shifts"),
        "customer_orders", "cash_shifts", ["cash_shift_id"], ["id"],
        source_schema=schema, referent_schema=schema, ondelete="SET NULL",
    )
    op.create_index(op.f("ix__customer_orders__cash_shift_id"), "customer_orders",
                     ["cash_shift_id"], schema=schema)
    # Unicidad del número dentro del turno, solo para filas que sí lo tienen (DINE_IN):
    op.create_index(
        "uq_customer_orders_shift_table_order_number", "customer_orders",
        ["cash_shift_id", "table_order_number"], unique=True, schema=schema,
        postgresql_where=sa.text("table_order_number IS NOT NULL"),
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "customer_orders"):
        return
    op.drop_index("uq_customer_orders_shift_table_order_number", "customer_orders", schema=schema)
    op.drop_index(op.f("ix__customer_orders__cash_shift_id"), "customer_orders", schema=schema)
    op.drop_constraint(op.f("fk__customer_orders__cash_shift_id__cash_shifts"),
                        "customer_orders", schema=schema, type_="foreignkey")
    op.drop_column("customer_orders", "table_order_number", schema=schema)
    op.drop_column("customer_orders", "cash_shift_id", schema=schema)
