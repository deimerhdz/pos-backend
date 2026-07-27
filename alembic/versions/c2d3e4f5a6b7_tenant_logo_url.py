"""tenant logo_url en shared.tenants

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
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
    if not _has_column("shared", "tenants", "logo_url"):
        op.add_column(
            "tenants",
            sa.Column("logo_url", sa.String(length=500), nullable=True),
            schema="shared",
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_column("shared", "tenants", "logo_url"):
        op.drop_column("tenants", "logo_url", schema="shared")
