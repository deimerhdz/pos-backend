"""084 variante presentacion

Revision ID: a9f7d0310f6b
Revises: da7581f7bb18
Create Date: 2026-09-17 12:00:00.000000

spec 084-fix-promociones-productos (data-model.md, research.md D1/D2). 100%
aditiva, sin contraparte destructiva.

Agrega `tenant.product_variants.presentation_id` (FK nullable a
`tenant.presentations.id`, `ON DELETE SET NULL`) y el `UNIQUE (product_id,
presentation_id)` que impide que dos variantes del mismo producto compartan
presentación (FR-006). Ninguna fila existente se modifica: toda variante ya
creada nace con `presentation_id = NULL` (Principio VII, no retroactivo).

El `downgrade` hace `DROP CONSTRAINT`/`DROP COLUMN`, sin paso de datos que
revertir (mismo criterio que `da7581f7bb18`/spec 083).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = 'a9f7d0310f6b'
down_revision: Union[str, Sequence[str], None] = 'da7581f7bb18'
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
            "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants") or not _has_table(schema, "presentations"):
        return
    if _has_column(schema, "product_variants", "presentation_id"):
        return

    op.add_column(
        "product_variants",
        sa.Column("presentation_id", sa.UUID(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", "presentations",
        ["presentation_id"], ["id"],
        source_schema=schema, referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq__product_variants__product_id__presentation_id",
        "product_variants",
        ["product_id", "presentation_id"],
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants"):
        return
    if not _has_column(schema, "product_variants", "presentation_id"):
        return

    op.drop_constraint(
        "uq__product_variants__product_id__presentation_id",
        "product_variants", schema=schema, type_="unique",
    )
    op.drop_constraint(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", schema=schema, type_="foreignkey",
    )
    op.drop_column("product_variants", "presentation_id", schema=schema)
