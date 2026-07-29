"""tenant invoice_prefix en shared.tenants

Prefijo del consecutivo de facturación. Cada prefijo lleva su propia numeración
(`invoice_counters`), así que a futuro mapea a una resolución DIAN.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
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
    if not _has_column("shared", "tenants", "invoice_prefix"):
        op.add_column(
            "tenants",
            sa.Column("invoice_prefix", sa.String(length=20), nullable=True),
            schema="shared",
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_column("shared", "tenants", "invoice_prefix"):
        op.drop_column("tenants", "invoice_prefix", schema="shared")
