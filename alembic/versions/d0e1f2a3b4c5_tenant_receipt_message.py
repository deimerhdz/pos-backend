"""tenant receipt_message en shared.tenants

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
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


def upgrade() -> None:
    """Upgrade schema. shared.tenants es una tabla única (no per-tenant)."""
    if not _has_column("shared", "tenants", "receipt_message"):
        op.add_column(
            "tenants",
            sa.Column("receipt_message", sa.String(length=255), nullable=True),
            schema="shared",
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_column("shared", "tenants", "receipt_message"):
        op.drop_column("tenants", "receipt_message", schema="shared")
