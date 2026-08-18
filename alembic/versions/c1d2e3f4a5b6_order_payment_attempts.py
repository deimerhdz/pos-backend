"""pagos de órdenes en mesa: order_payment_attempts + payment_methods.payment_info

Revision ID: c1d2e3f4a5b6
Revises: b3c4d5e6f7a8
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Ancla: si no existe 'products' el schema no está inicializado (scratch).
    if not _has_table(schema, "products"):
        return

    op.add_column(
        "payment_methods",
        sa.Column("payment_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=schema,
    )

    op.create_table(
        "order_payment_attempts",
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("payment_method_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pendiente"),
        sa.Column("amount_received", sa.Numeric(12, 2), nullable=True),
        sa.Column("change_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("receipt_file_url", sa.String(length=500), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pendiente', 'confirmado', 'rechazado')",
            name=op.f("ck__order_payment_attempts__ck_order_payment_attempt_status"),
        ),
        sa.CheckConstraint(
            "status != 'rechazado' OR rejection_reason IS NOT NULL",
            name=op.f("ck__order_payment_attempts__ck_order_payment_attempt_rejection_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{schema}.customer_orders.id"],
            name=op.f("fk__order_payment_attempts__order_id__customer_orders"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_method_id"], [f"{schema}.payment_methods.id"],
            name=op.f("fk__order_payment_attempts__payment_method_id__payment_methods"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__order_payment_attempts")),
        schema=schema,
    )
    op.create_index(
        op.f("ix__order_payment_attempts__order_id"),
        "order_payment_attempts", ["order_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__order_payment_attempts__payment_method_id"),
        "order_payment_attempts", ["payment_method_id"], schema=schema,
    )
    # A lo sumo un intento 'pendiente' por orden a la vez (FR-015a).
    op.create_index(
        "idx_pending_payment_attempt_per_order",
        "order_payment_attempts", ["order_id"],
        unique=True, schema=schema,
        postgresql_where=text("status = 'pendiente'"),
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_index("idx_pending_payment_attempt_per_order", "order_payment_attempts", schema=schema)
    op.drop_index(
        op.f("ix__order_payment_attempts__payment_method_id"),
        "order_payment_attempts", schema=schema,
    )
    op.drop_index(
        op.f("ix__order_payment_attempts__order_id"),
        "order_payment_attempts", schema=schema,
    )
    op.drop_table("order_payment_attempts", schema=schema)
    op.drop_column("payment_methods", "payment_info", schema=schema)
