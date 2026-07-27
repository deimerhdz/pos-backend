"""compras: recepción parcial (purchases.status, purchase_items.received_quantity)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'a6b7c8d9e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "purchases"):
        return
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    q = preparer.format_schema(schema)

    op.add_column("purchases", sa.Column("status", sa.String(12), nullable=False,
                                         server_default="received"), schema=schema)
    op.create_check_constraint(
        "ck_purchase_status", "purchases",
        "status IN ('draft', 'partial', 'received')", schema=schema,
    )

    op.add_column("purchase_items", sa.Column("received_quantity", sa.Numeric(12, 3),
                                              nullable=False, server_default="0"), schema=schema)
    # Backfill: las compras existentes ya dieron alta de stock (recibidas por completo).
    op.execute(f"UPDATE {q}.purchase_items SET received_quantity = quantity")
    op.create_check_constraint(
        "ck_purchase_item_received_range", "purchase_items",
        "received_quantity >= 0 AND received_quantity <= quantity", schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "purchases"):
        return
    op.drop_constraint(op.f("ck__purchase_items__ck_purchase_item_received_range"),
                       "purchase_items", type_="check", schema=schema)
    op.drop_column("purchase_items", "received_quantity", schema=schema)
    op.drop_constraint(op.f("ck__purchases__ck_purchase_status"), "purchases",
                       type_="check", schema=schema)
    op.drop_column("purchases", "status", schema=schema)
