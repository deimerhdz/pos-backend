"""orden de presentaciones: product_variants.display_order

Revision ID: c8ff3a5551cb
Revises: 187e491e597a
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'c8ff3a5551cb'
down_revision: Union[str, Sequence[str], None] = '187e491e597a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants"):
        return

    op.add_column(
        "product_variants",
        sa.Column("display_order", sa.Integer(), nullable=True),
        schema=schema,
    )

    # Backfill (spec 042, FR-009/SC-004): reproduce el orden que hoy YA ve el
    # comensal en el Menú QR (creación/inserción, vía la relación ORM sin
    # order_by explícito) -- no el orden alfabético que hoy muestra por
    # accidente `GET /products/{id}/variants` (`.order_by(ProductVariant.name)`
    # en catalog/router.py, corregido en el mismo cambio que introduce esta
    # migración). Ordenar por `id` en vez de `created_at` evita empates cuando
    # varias variantes de un mismo producto se crean en la misma transacción
    # (p. ej. `ensure_default_variant`, spec 002).
    op.execute(f"""
        UPDATE {schema}.product_variants pv
        SET display_order = sub.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY id) AS rn
            FROM {schema}.product_variants
        ) sub
        WHERE pv.id = sub.id
    """)

    op.alter_column("product_variants", "display_order", nullable=False, schema=schema)

    # UNIQUE simple (no diferible): el endpoint de reordenamiento (spec 042)
    # reasigna el orden completo de un producto en dos pasadas (valores
    # negativos temporales, luego los definitivos) precisamente para no
    # depender de un UNIQUE diferible -- SQLite (usado por los
    # characterization tests) no soporta diferir UNIQUE, solo FOREIGN KEY.
    op.create_unique_constraint(
        "uq__product_variants__product_id__display_order",
        "product_variants",
        ["product_id", "display_order"],
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants"):
        return
    op.drop_constraint(
        "uq__product_variants__product_id__display_order",
        "product_variants",
        schema=schema,
        type_="unique",
    )
    op.drop_column("product_variants", "display_order", schema=schema)
