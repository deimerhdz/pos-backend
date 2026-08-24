"""add_tenant_timezone

Zona horaria IANA del negocio de cada tenant (spec 030, reapertura de A-46).
`server_default='America/Bogota'` cubre a todo tenant existente sin backfill.

Revision ID: 2d2c3090473f
Revises: e3f4a5b6c7d8
Create Date: 2026-08-24 08:53:17.966686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '2d2c3090473f'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
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
    """Upgrade schema. shared.tenants es una tabla única (no per-tenant), así que
    esta migración **no** va envuelta en `for_each_tenant_schema`."""
    if not _has_column("shared", "tenants", "timezone"):
        op.add_column(
            "tenants",
            sa.Column(
                "timezone", sa.String(length=255), nullable=False,
                server_default="America/Bogota",
            ),
            schema="shared",
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_column("shared", "tenants", "timezone"):
        op.drop_column("tenants", "timezone", schema="shared")
