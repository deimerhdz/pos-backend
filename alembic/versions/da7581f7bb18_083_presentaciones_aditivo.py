"""083 presentaciones aditivo

Revision ID: da7581f7bb18
Revises: ef0abdf40889
Create Date: 2026-09-16 12:00:00.000000

spec 083-presentaciones-y-promociones (data-model.md §Migración Alembic,
research.md D1/D2/D8). 100% aditiva, sin contraparte destructiva.

Crea `tenant.presentations` (catálogo global de nombres de presentación,
espejo de `option_groups`) y `tenant.category_presentations` (tabla puente
categoría↔presentación, espejo de `variant_option_groups`). Por cada schema
de tenant, siembra una `Presentation` activa por cada `product_variants.name`
distinto ya existente (FR-019, comparación exacta de texto, sin normalizar) --
ninguna fila se inserta en `category_presentations` (la siembra no crea
asociaciones).

El `downgrade` hace `DROP TABLE` de ambas tablas nuevas sin intentar revertir
selectivamente el paso de datos (mismo criterio que `063a`/`94144eaa60b5`).
Ninguna `Sale`/`Invoice`/`CustomerOrder` se toca (Principio VII).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = 'da7581f7bb18'
down_revision: Union[str, Sequence[str], None] = 'ef0abdf40889'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


# --------------------------------------------------------------------------- #
# Paso de datos — función pura, testeable sin PostgreSQL
# (test_presentations_migration.py), mismo patrón que `387ef3e638cd` (`063a`).
# --------------------------------------------------------------------------- #
def seed_presentations_sql(schema: str) -> str:
    """SQL de la siembra inicial (FR-019): una `Presentation` activa por cada
    `product_variants.name` distinto, comparación exacta de texto."""
    return (
        f'INSERT INTO "{schema}".presentations (id, name, active, created_at, updated_at) '
        f"SELECT gen_random_uuid(), v.name, true, now(), now() "
        f'FROM (SELECT DISTINCT name FROM "{schema}".product_variants) AS v'
    )


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "categories"):
        return

    op.create_table(
        "presentations",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__presentations")),
        sa.UniqueConstraint("name", name=op.f("uq__presentations__name")),
        schema=schema,
    )

    op.create_table(
        "category_presentations",
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("presentation_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["category_id"], [f"{schema}.categories.id"],
            name=op.f("fk__category_presentations__category_id__categories"),
            ondelete="CASCADE",
        ),
        # Sin ondelete: `Presentation` no tiene borrado físico (D4).
        sa.ForeignKeyConstraint(
            ["presentation_id"], [f"{schema}.presentations.id"],
            name=op.f("fk__category_presentations__presentation_id__presentations"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__category_presentations")),
        sa.UniqueConstraint(
            "category_id", "presentation_id",
            name="uq__category_presentations__category_id__presentation_id",
        ),
        schema=schema,
    )
    op.create_index(
        op.f("ix__category_presentations__category_id"), "category_presentations",
        ["category_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__category_presentations__presentation_id"), "category_presentations",
        ["presentation_id"], schema=schema,
    )

    if _has_table(schema, "product_variants"):
        op.execute(text(seed_presentations_sql(schema)))


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "category_presentations"):
        return

    op.drop_index(
        op.f("ix__category_presentations__presentation_id"),
        table_name="category_presentations", schema=schema,
    )
    op.drop_index(
        op.f("ix__category_presentations__category_id"),
        table_name="category_presentations", schema=schema,
    )
    op.drop_table("category_presentations", schema=schema)
    op.drop_table("presentations", schema=schema)
