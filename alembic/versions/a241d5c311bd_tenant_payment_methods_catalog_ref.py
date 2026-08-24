"""catálogo de métodos de pago: tenant.payment_methods gana catalog_id + is_complete

Revision ID: a241d5c311bd
Revises: d6953c4dcf45
Create Date: 2026-08-24 00:00:00.000001

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'a241d5c311bd'
down_revision: Union[str, Sequence[str], None] = 'd6953c4dcf45'
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
def upgrade(schema: str) -> None:
    # Ancla: si no existe 'products' el schema no está inicializado (scratch).
    if not _has_table(schema, "products"):
        return

    if not _has_column(schema, "payment_methods", "catalog_id"):
        # Nullable a propósito (research.md Decisión 3): el backfill de la
        # migración de datos existentes (spec 032, FR-015/FR-015a) puebla esta
        # columna fila por fila antes de que la capa de aplicación exija el
        # valor para escrituras nuevas — forzar NOT NULL aquí rompería tenants
        # con datos existentes sin revisar todavía.
        op.add_column(
            "payment_methods",
            sa.Column("catalog_id", sa.UUID(), nullable=True),
            schema=schema,
        )
        op.create_foreign_key(
            op.f("fk__payment_methods__catalog_id__payment_method_catalog"),
            "payment_methods", "payment_method_catalog", ["catalog_id"], ["id"],
            source_schema=schema, referent_schema="shared",
        )
        # A lo sumo una fila por (tenant, catalog_id) para siempre — no
        # parcial: activar/desactivar alterna `active` sobre la misma fila,
        # nunca crea una fila nueva (FR-017; evita además que dos filas del
        # mismo catalog_id choquen contra `payment_methods.name` único, ya
        # que `name` se copia de `catalog.name`). Postgres no considera
        # iguales dos NULL, así que no bloquea las filas pre-backfill.
        op.create_unique_constraint(
            "uq_payment_method_catalog_id", "payment_methods", ["catalog_id"], schema=schema,
        )

    if not _has_column(schema, "payment_methods", "is_complete"):
        # Default `true`: las filas ya existentes (sin `catalog_id` todavía)
        # deben seguir disponibles en caja tal como estaban (FR-016) hasta
        # que el backfill (FR-015/FR-015a) recalcule el valor real de cada
        # una — `false` las habría vaciado del checkout de todos los
        # tenants desde el momento de aplicar esta migración.
        op.add_column(
            "payment_methods",
            sa.Column("is_complete", sa.Boolean(), nullable=False, server_default="true"),
            schema=schema,
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    if _has_column(schema, "payment_methods", "is_complete"):
        op.drop_column("payment_methods", "is_complete", schema=schema)
    if _has_column(schema, "payment_methods", "catalog_id"):
        op.drop_constraint(
            "uq_payment_method_catalog_id", "payment_methods", schema=schema, type_="unique",
        )
        op.drop_constraint(
            op.f("fk__payment_methods__catalog_id__payment_method_catalog"),
            "payment_methods", schema=schema, type_="foreignkey",
        )
        op.drop_column("payment_methods", "catalog_id", schema=schema)
