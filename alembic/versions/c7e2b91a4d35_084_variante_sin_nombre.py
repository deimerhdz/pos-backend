"""084 variante sin nombre

Revision ID: c7e2b91a4d35
Revises: a9f7d0310f6b
Create Date: 2026-09-20 12:00:00.000000

spec 084-fix-promociones-productos, enmienda 2026-09-20 (A-79, data-model.md
§Enmienda, research.md D10). Elimina `product_variants.name`: el nombre de una variante
pasa a ser el de su `Presentation`, que se vuelve obligatoria.

Por schema de tenant, en este orden:

1. Crea en el catálogo una `Presentation` activa por cada nombre libre de una variante
   sin presentación que aún no exista (coincidencia EXACTA de texto: `presentations.name`
   es UNIQUE sensible a mayúsculas, igual que lo era `UNIQUE(product_id, name)`, así que
   dentro de un producto dos nombres distintos nunca colapsan en la misma presentación).
2. Enlaza cada variante sin presentación con la fila de su nombre. Una presentación
   desactivada se reutiliza tal cual.
3. Verifica que no quede ninguna variante sin presentación (aborta si queda alguna).
4. `presentation_id` -> NOT NULL, FK -> ON DELETE RESTRICT, se elimina
   `uq__product_variants__product_id__name` y la columna `name`.

Las variantes que ya tenían `presentation_id` (spec 084 US1) no se modifican: su `name`
ya coincidía con el de la presentación por la cascada de esa versión.

`downgrade`: sin pérdida. Re-crea `name` desde `presentations.name` (dos variantes del
mismo producto nunca comparten presentación, así que los nombres no pueden chocar),
vuelve a poner `presentation_id` como nullable con FK ON DELETE SET NULL y re-crea la
unicidad por nombre. Las presentaciones creadas en el paso 1 no se borran al revertir.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = 'c7e2b91a4d35'
down_revision: Union[str, Sequence[str], None] = 'a9f7d0310f6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK = "fk__product_variants__presentation_id__presentations"
UQ_NAME = "uq__product_variants__product_id__name"


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f'"{schema}"."{table}"'}
    ).scalar() is not None


def _has_column(schema: str, table: str, column: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).scalar() is not None


# --------------------------------------------------------------------------- #
# Paso de datos -- funciones puras, testeables sin PostgreSQL
# (test_variant_sin_nombre_migration.py), mismo patrón que `da7581f7bb18`.
# --------------------------------------------------------------------------- #
def seed_free_names_sql(schema: str) -> str:
    """Una `Presentation` activa por cada nombre libre (variante sin presentación) que aún
    no exista en el catálogo; comparación exacta de texto."""
    return (
        f'INSERT INTO "{schema}".presentations (id, name, active, created_at, updated_at) '
        f"SELECT gen_random_uuid(), n.name, true, now(), now() "
        f'FROM (SELECT DISTINCT name FROM "{schema}".product_variants '
        f"      WHERE presentation_id IS NULL) AS n "
        f'WHERE NOT EXISTS (SELECT 1 FROM "{schema}".presentations p WHERE p.name = n.name)'
    )


def link_free_names_sql(schema: str) -> str:
    """Enlaza cada variante sin presentación con la fila del catálogo de su mismo nombre."""
    return (
        f'UPDATE "{schema}".product_variants SET presentation_id = ('
        f'SELECT p.id FROM "{schema}".presentations p '
        f"WHERE p.name = product_variants.name) "
        f"WHERE presentation_id IS NULL"
    )


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants") or not _has_table(schema, "presentations"):
        return
    if not _has_column(schema, "product_variants", "name"):
        return  # ya migrado

    bind = op.get_bind()

    # 1) Presentaciones faltantes para los nombres libres. 2) Enlazar.
    op.execute(seed_free_names_sql(schema))
    op.execute(link_free_names_sql(schema))
    # 3) Verificación.
    pending = bind.execute(
        text(f'SELECT count(*) FROM "{schema}".product_variants WHERE presentation_id IS NULL')
    ).scalar()
    if pending:
        raise RuntimeError(
            f"{schema}: {pending} variante(s) sin presentación tras el enlazado; migración abortada"
        )

    # 4) Endurecer y eliminar el nombre.
    op.execute(f'ALTER TABLE "{schema}".product_variants ALTER COLUMN presentation_id SET NOT NULL')
    op.execute(f'ALTER TABLE "{schema}".product_variants DROP CONSTRAINT IF EXISTS {FK}')
    op.execute(
        f'ALTER TABLE "{schema}".product_variants ADD CONSTRAINT {FK} '
        f'FOREIGN KEY (presentation_id) REFERENCES "{schema}".presentations (id) ON DELETE RESTRICT'
    )
    op.execute(f'ALTER TABLE "{schema}".product_variants DROP CONSTRAINT IF EXISTS {UQ_NAME}')
    op.execute(f'ALTER TABLE "{schema}".product_variants DROP COLUMN name')


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "product_variants"):
        return
    if _has_column(schema, "product_variants", "name"):
        return  # ya revertido

    op.execute(f'ALTER TABLE "{schema}".product_variants ADD COLUMN name VARCHAR(255)')
    op.execute(
        f'UPDATE "{schema}".product_variants pv SET name = p.name '
        f'FROM "{schema}".presentations p WHERE p.id = pv.presentation_id'
    )
    op.execute(f'ALTER TABLE "{schema}".product_variants ALTER COLUMN name SET NOT NULL')
    op.execute(
        f"ALTER TABLE \"{schema}\".product_variants ALTER COLUMN name SET DEFAULT 'Presentación única'"
    )
    op.execute(
        f'ALTER TABLE "{schema}".product_variants ADD CONSTRAINT {UQ_NAME} UNIQUE (product_id, name)'
    )
    op.execute(f'ALTER TABLE "{schema}".product_variants DROP CONSTRAINT IF EXISTS {FK}')
    op.execute(
        f'ALTER TABLE "{schema}".product_variants ADD CONSTRAINT {FK} '
        f'FOREIGN KEY (presentation_id) REFERENCES "{schema}".presentations (id) ON DELETE SET NULL'
    )
    op.execute(f'ALTER TABLE "{schema}".product_variants ALTER COLUMN presentation_id DROP NOT NULL')
