"""ajustes: products.available, sales.paid_amount/change_given, cash_partial_counts

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, Sequence[str], None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return

    op.add_column("products", sa.Column("available", sa.Boolean(), nullable=False,
                                        server_default="true"), schema=schema)

    op.add_column("sales", sa.Column("paid_amount", sa.Numeric(12, 2), nullable=True), schema=schema)
    op.add_column("sales", sa.Column("change_given", sa.Numeric(12, 2), nullable=True), schema=schema)

    op.create_table(
        "cash_partial_counts",
        sa.Column("cash_shift_id", sa.UUID(), nullable=False),
        sa.Column("counted_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("difference", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("counted_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cash_shift_id"], [f"{schema}.cash_shifts.id"],
            name=op.f("fk__cash_partial_counts__cash_shift_id__cash_shifts"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__cash_partial_counts")),
        schema=schema,
    )
    op.create_index(op.f("ix__cash_partial_counts__cash_shift_id"), "cash_partial_counts",
                    ["cash_shift_id"], schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_index(op.f("ix__cash_partial_counts__cash_shift_id"), "cash_partial_counts", schema=schema)
    op.drop_table("cash_partial_counts", schema=schema)
    op.drop_column("sales", "change_given", schema=schema)
    op.drop_column("sales", "paid_amount", schema=schema)
    op.drop_column("products", "available", schema=schema)
