"""administración: business_hours y audit_logs

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

revision: str = 'a6b7c8d9e0f1'
down_revision: Union[str, Sequence[str], None] = 'f5a6b7c8d9e0'
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

    op.create_table(
        "business_hours",
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=True),
        sa.Column("close_time", sa.Time(), nullable=True),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("day_of_week BETWEEN 0 AND 6",
                           name=op.f("ck__business_hours__ck_business_hours_dow")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__business_hours")),
        sa.UniqueConstraint("day_of_week", name="uq__business_hours__day_of_week"),
        schema=schema,
    )

    op.create_table(
        "audit_logs",
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__audit_logs")),
        schema=schema,
    )
    op.create_index(op.f("ix__audit_logs__entity"), "audit_logs", ["entity"], schema=schema)
    op.create_index(op.f("ix__audit_logs__at"), "audit_logs", ["at"], schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_index(op.f("ix__audit_logs__at"), "audit_logs", schema=schema)
    op.drop_index(op.f("ix__audit_logs__entity"), "audit_logs", schema=schema)
    op.drop_table("audit_logs", schema=schema)
    op.drop_table("business_hours", schema=schema)
