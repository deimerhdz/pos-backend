"""products: available_changed_at, available_changed_by_name

Spec 093-cajero-carta-agotado (data-model.md § Entidad Product, Migración):
agrega a `tenant.products` dos columnas nullable de trazabilidad para el
interruptor "Agotado" (`Product.available`, ya existente): cuándo fue el
último cambio y quién lo hizo (nombre, snapshot de texto -- no FK dura a
`shared.users`, igual que `AuditLog.user_name`, porque `users` vive en el
schema `shared` y `products` en el schema `tenant`). `NULL` en ambas
significa "nunca se tocó el interruptor dedicado" -- incluye todo producto
creado antes de esta spec; no se infiere retroactivamente ningún valor.

Rollback: `DROP COLUMN` de ambas, por cada schema de tenant. Sin FKs
entrantes ni otra tabla que dependa de ellas (data-model.md § Rollback).

Revision ID: 2d50957395dc
Revises: c91a4e7b2d58
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = '2d50957395dc'
down_revision: Union[str, Sequence[str], None] = 'c91a4e7b2d58'
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


@for_each_tenant_schema
def _upgrade_tenant_schema(schema: str) -> None:
    # Ancla: si no existe 'products' el schema no está inicializado (scratch).
    if not _has_table(schema, "products"):
        return
    if not _has_column(schema, "products", "available_changed_at"):
        op.add_column(
            "products",
            sa.Column("available_changed_at", sa.DateTime(), nullable=True),
            schema=schema,
        )
    if not _has_column(schema, "products", "available_changed_by_name"):
        op.add_column(
            "products",
            sa.Column("available_changed_by_name", sa.String(length=150), nullable=True),
            schema=schema,
        )


@for_each_tenant_schema
def _downgrade_tenant_schema(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    if _has_column(schema, "products", "available_changed_by_name"):
        op.drop_column("products", "available_changed_by_name", schema=schema)
    if _has_column(schema, "products", "available_changed_at"):
        op.drop_column("products", "available_changed_at", schema=schema)


def upgrade() -> None:
    """Upgrade schema."""
    _upgrade_tenant_schema()


def downgrade() -> None:
    """Downgrade schema."""
    _downgrade_tenant_schema()
