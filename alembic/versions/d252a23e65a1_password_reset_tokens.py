"""password_reset_tokens

Recuperación y cambio de contraseña (spec 031): columna
`shared.users.tokens_valid_after` (corte de sesiones) y tabla nueva
`shared.password_reset_tokens` (enlace de un solo uso).

Revision ID: d252a23e65a1
Revises: 2d2c3090473f
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd252a23e65a1'
down_revision: Union[str, Sequence[str], None] = '2d2c3090473f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(schema: str, table: str, column: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": schema, "t": table, "c": column},
    ).scalar() is not None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": table},
    ).scalar() is not None


def upgrade() -> None:
    """Upgrade schema. shared.users/password_reset_tokens no son per-tenant,
    así que esta migración **no** va envuelta en `for_each_tenant_schema`."""
    if not _has_column("shared", "users", "tokens_valid_after"):
        op.add_column(
            "users",
            sa.Column("tokens_valid_after", sa.DateTime(), nullable=True),
            schema="shared",
        )

    if not _has_table("shared", "password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("email_snapshot", sa.String(length=255), nullable=False),
            sa.Column("issued_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("invalidated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["user_id"], ["shared.users.id"],
                name="fk__password_reset_tokens__user_id__users",
            ),
            sa.PrimaryKeyConstraint("id", name="pk__password_reset_tokens"),
            schema="shared",
        )
        op.create_index(
            "ix__password_reset_tokens__user_id",
            "password_reset_tokens",
            ["user_id"],
            unique=False,
            schema="shared",
        )
        op.create_index(
            "ix__password_reset_tokens__token_hash",
            "password_reset_tokens",
            ["token_hash"],
            unique=True,
            schema="shared",
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table("shared", "password_reset_tokens"):
        op.drop_table("password_reset_tokens", schema="shared")

    if _has_column("shared", "users", "tokens_valid_after"):
        op.drop_column("users", "tokens_valid_after", schema="shared")
