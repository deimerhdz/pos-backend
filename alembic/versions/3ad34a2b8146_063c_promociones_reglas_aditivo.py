"""063c promociones reglas aditivo

Revision ID: 3ad34a2b8146
Revises: ba4b6bd573a6
Create Date: 2026-09-01 00:00:00.000000

spec 063 (revisión 2026-09-01, partición `Promoción`/`Regla`) — Incremento G1
(100% aditivo, sin tocar el motor). Ver
`specs/063-promociones-por-variante/data-model.md` §"Entidad nueva: PromotionRule"
y `contracts/migracion.md` §1. No es un cambio de comportamiento de producción
(spec.md §"Cambios de comportamiento", ítem 9): ninguna de las dos ramas de
feature de este refactor está en `main`.

Esta revisión NO borra `promotions.type`/`value`/`min_qty` ni
`promotion_variants.promotion_id` — siguen intactas y el motor
(`evaluate_variant_sets`) sigue leyéndolas directo hasta el Incremento G2. El
retiro de esas columnas va en la revisión destructiva `063d` (Incremento J),
cuando ningún módulo las referencia ya.

Cambios:
- tabla nueva `promotion_rules` (hija de `promotions`): `type`, `value`,
  `min_qty` — la combinación que hoy vive directo en `Promotion`. **Sin
  `CHECK` de valores en `type`**: Postgres no admite subconsultas en un
  `CHECK`, así que el escape `OR status='finished'` que sí usa
  `ck_promotion_type` (columna `status` de la misma fila) no es replicable
  aquí — `promotion_rules` no tiene columna de estado propia por diseño
  (corrección de implementación 2026-09-01 del hallazgo F1 de
  `/speckit-analyze`: la primera propuesta, un `CHECK ... OR EXISTS(...)`,
  resultó ser SQL inválido, verificado empíricamente contra Postgres real
  con el error `cannot use subquery in check constraint`);
- `promotion_variants.promotion_rule_id` (FK, nullable en esta revisión);
- PASO DE DATOS (`migrate_promotion_rules_data`): una `PromotionRule` por
  cada `Promotion` existente (migración 1:1 — toda promoción actual tiene
  exactamente una combinación), copiando `type`/`value`/`min_qty` sin
  filtrar por `status` (una `Finalizada` de tipo legado también gana su
  regla histórica); `promotion_variants` se repunta a la regla nueva.

El `downgrade` revierte solo lo aditivo; el paso de datos NO se revierte
(las filas de `promotion_rules` se pierden, `promotion_variants.promotion_id`
nunca se tocó). Ninguna `Sale`/`Invoice`/`CustomerOrder` emitida cambia de
importe en ningún sentido (Principio VII): el paso de datos opera solo sobre
`promotions`/`promotion_variants`.
"""
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID

from app.scripts.tenant import for_each_tenant_schema


# revision identifiers, used by Alembic.
revision: str = '3ad34a2b8146'
down_revision: Union[str, Sequence[str], None] = 'ba4b6bd573a6'
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


# --------------------------------------------------------------------------- #
# Paso de datos — función pura, testeable sin PostgreSQL (T041/T042/T051).
#
# `schema=None` -> tablas sin cualificar (SQLite en memoria de los
# characterization tests, con `schema_translate_map={"tenant": None}`).
# `schema="<tenant>"` -> tablas cualificadas por schema (migración real).
# Mismo patrón que `387ef3e638cd_063a_promociones_por_conjunto_aditivo.py`.
# --------------------------------------------------------------------------- #
def _data_tables(schema: Optional[str]) -> dict:
    md = sa.MetaData()
    return {
        "promotions": sa.Table(
            "promotions", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("type", sa.String(50)),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("min_qty", sa.Integer),
            schema=schema,
        ),
        "promotion_rules": sa.Table(
            "promotion_rules", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", UUID(as_uuid=True)),
            sa.Column("type", sa.String(50)),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("min_qty", sa.Integer),
            schema=schema,
        ),
        "promotion_variants": sa.Table(
            "promotion_variants", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", UUID(as_uuid=True)),
            sa.Column("promotion_rule_id", UUID(as_uuid=True)),
            schema=schema,
        ),
    }


def migrate_promotion_rules_data(bind, schema: Optional[str] = None) -> None:
    """Paso de datos de `063c` (data-model.md §"upgrade() — paso 5",
    contracts/migracion.md §1).

    Por cada `Promotion` existente (**sin filtrar por `status`**: una
    `Finalizada` de tipo legado también gana su regla histórica) crea
    **una** `PromotionRule` con su `type`/`value`/`min_qty`, y repunta las
    filas de `promotion_variants` de esa promoción a la regla nueva.
    Migración 1:1 sin ambigüedad: en el modelo plano, toda promoción tiene
    exactamente una combinación.
    """
    import uuid as _uuid

    t = _data_tables(schema)
    promotions = t["promotions"]
    promotion_rules = t["promotion_rules"]
    promotion_variants = t["promotion_variants"]

    rows = bind.execute(
        sa.select(
            promotions.c.id, promotions.c.type,
            promotions.c.value, promotions.c.min_qty,
        )
    ).fetchall()

    for promo_id, type_, value, min_qty in rows:
        rule_id = _uuid.uuid4()
        bind.execute(
            sa.insert(promotion_rules),
            {
                "id": rule_id, "promotion_id": promo_id,
                "type": type_, "value": value, "min_qty": min_qty,
            },
        )
        bind.execute(
            sa.update(promotion_variants)
            .where(promotion_variants.c.promotion_id == promo_id)
            .values(promotion_rule_id=rule_id)
        )


# --------------------------------------------------------------------------- #
# Migración
# --------------------------------------------------------------------------- #
@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return
    if _has_table(schema, "promotion_rules"):
        return  # reintento idempotente sobre un tenant ya migrado

    op.create_table(
        "promotion_rules",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column(
            "value", sa.Numeric(12, 2), nullable=False,
            server_default="0",
        ),
        sa.Column("min_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("value >= 0", name=op.f("ck__promotion_rules__ck_promotion_rule_value_positive")),
        sa.CheckConstraint("min_qty >= 1", name=op.f("ck__promotion_rules__ck_promotion_rule_min_qty")),
        sa.CheckConstraint(
            "type <> 'percent' OR value <= 100",
            name=op.f("ck__promotion_rules__ck_promotion_rule_percent_range"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_rules__promotion_id__promotions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_rules")),
        schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_rules__promotion_id"),
        "promotion_rules", ["promotion_id"], schema=schema,
    )

    op.add_column(
        "promotion_variants",
        sa.Column("promotion_rule_id", sa.UUID(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        op.f("fk__promotion_variants__promotion_rule_id__promotion_rules"),
        "promotion_variants", "promotion_rules", ["promotion_rule_id"], ["id"],
        source_schema=schema, referent_schema=schema, ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix__promotion_variants__promotion_rule_id"),
        "promotion_variants", ["promotion_rule_id"], schema=schema,
    )

    # --- PASO DE DATOS (con promotions.type/value/min_qty todavía presentes) --- #
    migrate_promotion_rules_data(op.get_bind(), schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return
    if not _has_table(schema, "promotion_rules"):
        return

    # El paso de datos NO se revierte (data-model.md §"downgrade()"): las
    # filas de `promotion_rules` se pierden; `promotion_variants.promotion_id`
    # nunca se tocó aquí, así que el conjunto de variantes de cada promoción
    # sigue intacto por esa vía.
    op.drop_index(
        op.f("ix__promotion_variants__promotion_rule_id"),
        table_name="promotion_variants", schema=schema,
    )
    op.drop_constraint(
        op.f("fk__promotion_variants__promotion_rule_id__promotion_rules"),
        "promotion_variants", type_="foreignkey", schema=schema,
    )
    op.drop_column("promotion_variants", "promotion_rule_id", schema=schema)

    op.drop_index(
        op.f("ix__promotion_rules__promotion_id"),
        table_name="promotion_rules", schema=schema,
    )
    op.drop_table("promotion_rules", schema=schema)
