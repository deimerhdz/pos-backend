"""user_invitations

Alta de usuarios internos por invitación (spec 037): tabla nueva
`shared.user_invitations`. Vive en `shared` (referencia `Tenant`/`Role`, que
tampoco son per-tenant) — igual que `password_reset_tokens` (spec 031), esta
migración **no** va envuelta en `for_each_tenant_schema`.

Revision ID: 205f518df786
Revises: 5a77a91b482d
Create Date: 2026-08-25 17:09:43.470843

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '205f518df786'
down_revision: Union[str, Sequence[str], None] = '5a77a91b482d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": table},
    ).scalar() is not None


def upgrade() -> None:
    """Upgrade schema. shared.user_invitations no es per-tenant, así que esta
    migración **no** va envuelta en `for_each_tenant_schema`."""
    if not _has_table("shared", "user_invitations"):
        op.create_table(
            "user_invitations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=10), nullable=False, server_default="pending"),
            sa.Column("sent_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("consumed_at", sa.DateTime(), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["tenant_id"], ["shared.tenants.id"],
                name="fk__user_invitations__tenant_id__tenants",
            ),
            sa.ForeignKeyConstraint(
                ["role_id"], ["shared.roles.id"],
                name="fk__user_invitations__role_id__roles",
            ),
            sa.PrimaryKeyConstraint("id", name="pk__user_invitations"),
            sa.CheckConstraint(
                "status IN ('pending', 'consumed', 'cancelled')",
                name="ck_user_invitations_status",
            ),
            schema="shared",
        )
        op.create_index(
            "ix__user_invitations__tenant_id",
            "user_invitations",
            ["tenant_id"],
            unique=False,
            schema="shared",
        )
        # A lo sumo una invitación 'pending' por (tenant, correo) a la vez
        # (FR-015, research.md Decisión 3).
        op.create_index(
            "idx_pending_invitation_per_tenant_email",
            "user_invitations",
            ["tenant_id", "email"],
            unique=True,
            schema="shared",
            postgresql_where=text("status = 'pending'"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table("shared", "user_invitations"):
        op.drop_table("user_invitations", schema="shared")
