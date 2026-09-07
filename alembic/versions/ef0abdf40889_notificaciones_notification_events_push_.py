"""notificaciones: notification_events, push_subscriptions, notification_channel_preferences

Spec 077-notificaciones-tiempo-real (data-model.md § Migración):
1. `shared.tenants.notification_retention_days` (columna nueva, server_default
   90 -- ningún tenant existente queda en NULL).
2. `tenant.notification_events`, `tenant.push_subscriptions`,
   `tenant.notification_channel_preferences`, creadas por cada schema de
   tenant (mismo mecanismo que el resto de tablas tenant-scoped).

Rollback: `DROP TABLE` en orden inverso (3, 2, 1) + `DROP COLUMN` en
`shared.tenants`. Sin FKs duras hacia estas tablas (data-model.md § Rollback).

Revision ID: ef0abdf40889
Revises: 8421e9a2b45a
Create Date: 2026-09-07 09:06:29.818162

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = 'ef0abdf40889'
down_revision: Union[str, Sequence[str], None] = '8421e9a2b45a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


def _has_column(schema: str, table: str, column: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": schema, "t": table, "c": column},
    ).scalar() is not None


def _upgrade_shared() -> None:
    if not _has_column("shared", "tenants", "notification_retention_days"):
        op.add_column(
            "tenants",
            sa.Column(
                "notification_retention_days", sa.Integer(), nullable=False,
                server_default="90",
            ),
            schema="shared",
        )


def _downgrade_shared() -> None:
    if _has_column("shared", "tenants", "notification_retention_days"):
        op.drop_column("tenants", "notification_retention_days", schema="shared")


@for_each_tenant_schema
def _upgrade_tenant_schema(schema: str) -> None:
    # Ancla: si no existe 'products' el schema no está inicializado (scratch).
    if not _has_table(schema, "products"):
        return

    op.create_table(
        "notification_events",
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("related_entity_type", sa.String(length=50), nullable=False),
        sa.Column("related_entity_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("purge_at", sa.DateTime(), nullable=False),
        sa.Column("attended_at", sa.DateTime(), nullable=True),
        sa.Column("attended_by_user_id", sa.UUID(), nullable=True),
        sa.Column("delivery", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__notification_events")),
        schema=schema,
    )
    op.create_index(
        op.f("ix__notification_events__created_at"), "notification_events",
        ["created_at"], schema=schema,
    )
    op.create_index(
        op.f("ix__notification_events__purge_at"), "notification_events",
        ["purge_at"], schema=schema,
    )

    op.create_table(
        "push_subscriptions",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("p256dh_key", sa.String(length=255), nullable=False),
        sa.Column("auth_key", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq__push_subscriptions__endpoint")),
        schema=schema,
    )

    op.create_table(
        "notification_channel_preferences",
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__notification_channel_preferences")),
        sa.UniqueConstraint(
            "event_type", "channel",
            name=op.f("uq__notification_channel_preferences__event_type_channel"),
        ),
        schema=schema,
    )


@for_each_tenant_schema
def _downgrade_tenant_schema(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_table("notification_channel_preferences", schema=schema)
    op.drop_table("push_subscriptions", schema=schema)
    op.drop_index(op.f("ix__notification_events__purge_at"), "notification_events", schema=schema)
    op.drop_index(op.f("ix__notification_events__created_at"), "notification_events", schema=schema)
    op.drop_table("notification_events", schema=schema)


def upgrade() -> None:
    """Upgrade schema."""
    _upgrade_shared()
    _upgrade_tenant_schema()


def downgrade() -> None:
    """Downgrade schema."""
    _downgrade_tenant_schema()
    _downgrade_shared()
