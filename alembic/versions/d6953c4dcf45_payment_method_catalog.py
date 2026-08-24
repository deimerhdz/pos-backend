"""catálogo de métodos de pago administrado por el Super Admin: shared.payment_method_catalog

Revision ID: d6953c4dcf45
Revises: 2d2c3090473f
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'd6953c4dcf45'
down_revision: Union[str, Sequence[str], None] = '2d2c3090473f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


def upgrade() -> None:
    """Upgrade schema. shared.payment_method_catalog es una tabla única (no per-tenant) —
    sin seed de datos: el seed inicial (Efectivo/Nequi/Transferencia Bancolombia) es su
    propia migración de datos de producción, ver {rev}_seed_payment_method_catalog.py
    (spec 032, Principio VI: no mezclar creación de esquema con migración de datos)."""
    if _has_table("shared", "payment_method_catalog"):
        return

    op.create_table(
        "payment_method_catalog",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "type IN ('cash', 'card', 'transfer', 'other')",
            name=op.f("ck__payment_method_catalog__ck_payment_method_catalog_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__payment_method_catalog")),
        sa.UniqueConstraint("name", name=op.f("uq__payment_method_catalog__name")),
        schema="shared",
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table("shared", "payment_method_catalog"):
        return
    op.drop_table("payment_method_catalog", schema="shared")
