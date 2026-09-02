"""063d promociones reglas destructivo

Revision ID: 94b7e35f5e5e
Revises: 3ad34a2b8146
Create Date: 2026-09-01 00:00:00.000000

spec 063 (revisión 2026-09-01, partición `Promoción`/`Regla`) — Incremento J
(revisión **destructiva**). Ver
`specs/063-promociones-por-variante/data-model.md`
§"063d_promociones_reglas_destructivo.py" y `contracts/migracion.md`. No es
un cambio de comportamiento de producción (spec.md §"Cambios de
comportamiento", ítem 9): ninguna de las dos ramas de feature de este
refactor está en `main`.

Se aplica cuando ningún módulo referencia ya `promotions.type`/`value`/
`min_qty` ni `promotion_variants.promotion_id` (Incrementos G1/G2/H/I ya
migraron el motor, el CRUD y el frontend a leer/escribir exclusivamente
`PromotionRule`/`promotion_rule_id`). Borra:
- `promotion_variants.promotion_id` (+ FK + índice) — `promotion_rule_id`
  pasa a `NOT NULL`; `UNIQUE(promotion_rule_id, product_variant_id)`
  reemplaza a `UNIQUE(promotion_id, product_variant_id)`;
- `promotions.type`, `promotions.value`, `promotions.min_qty` (+ sus 4
  `CHECK`s: `ck_promotion_type`, `ck_promotion_value_positive`,
  `ck_promotion_min_qty`, `ck_promotion_percent_range`) — la combinación
  vive exclusivamente en `promotion_rules` desde `063c`.

**No toca ninguna `Sale` / `Invoice` / `CustomerOrder` emitida** (Principio
VII). El `downgrade` recrea la estructura vacía y repuebla desde
`promotion_rules` — **con la misma limitación ya aceptada para el downgrade
de `063b`**: una promoción con más de una regla (posible desde que el
Incremento H expuso la creación multi-regla) no puede aplanarse de vuelta a
una sola fila de `promotions.type/value/min_qty` sin perder información; en
ese caso el downgrade dis a esa promoción con las columnas `NULL` y deja
constancia en el log de migración, en vez de adivinar cuál regla priorizar.
"""
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import UUID

from app.scripts.tenant import for_each_tenant_schema


# revision identifiers, used by Alembic.
revision: str = '94b7e35f5e5e'
down_revision: Union[str, Sequence[str], None] = '3ad34a2b8146'
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
# Paso de datos del downgrade — repuebla promotions.type/value/min_qty y
# promotion_variants.promotion_id desde promotion_rules. Función pura,
# testeable sin PostgreSQL, mismo patrón que 063a/063c.
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


def downgrade_flatten_rules_data(bind, schema: Optional[str] = None) -> None:
    """Repuebla `promotions.type/value/min_qty` y `promotion_variants.
    promotion_id` desde `promotion_rules`, una promoción a la vez.

    Si una promoción tiene **exactamente una** regla (el caso común — toda
    promoción migrada por `063c` y nunca tocada por el CRUD multi-regla),
    la reconstrucción es exacta. Si tiene **más de una** (creada o editada
    después de que el Incremento H expuso `rules[]`), no hay una sola
    combinación que la represente: se deja `type`/`value`/`min_qty` en
    `NULL` y se avisa por log — mismo criterio ya aceptado para el downgrade
    de `063b` (recrea estructura, no garantiza datos equivalentes cuando la
    operación no es reversible sin pérdida).
    """
    import logging

    t = _data_tables(schema)
    promotions = t["promotions"]
    promotion_rules = t["promotion_rules"]
    promotion_variants = t["promotion_variants"]

    promo_ids = [row[0] for row in bind.execute(sa.select(promotions.c.id))]
    for promo_id in promo_ids:
        rules = bind.execute(
            sa.select(
                promotion_rules.c.id, promotion_rules.c.type,
                promotion_rules.c.value, promotion_rules.c.min_qty,
            ).where(promotion_rules.c.promotion_id == promo_id)
        ).fetchall()

        if len(rules) == 1:
            rule_id, type_, value, min_qty = rules[0]
            bind.execute(
                sa.update(promotions).where(promotions.c.id == promo_id)
                .values(type=type_, value=value, min_qty=min_qty)
            )
            bind.execute(
                sa.update(promotion_variants)
                .where(promotion_variants.c.promotion_rule_id == rule_id)
                .values(promotion_id=promo_id)
            )
        elif len(rules) > 1:
            logging.getLogger(__name__).warning(
                "downgrade 063d: la promoción %s tiene %d reglas — no se "
                "puede aplanar a type/value/min_qty sin pérdida; queda con "
                "esas columnas en NULL. Recréala a mano si hace falta "
                "seguir en el modelo plano.",
                promo_id, len(rules),
            )
            for rule_id, *_ in rules:
                bind.execute(
                    sa.update(promotion_variants)
                    .where(promotion_variants.c.promotion_rule_id == rule_id)
                    .values(promotion_id=promo_id)
                )
        # len(rules) == 0: promoción sin reglas (conjunto vaciado por FR-011
        # hasta quedar sin ninguna variante elegible en ninguna regla, o
        # promoción recién creada sin guardar) — type/value/min_qty quedan
        # NULL, no hay nada que repuntar en promotion_variants.


# --------------------------------------------------------------------------- #
# Migración
# --------------------------------------------------------------------------- #
@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return
    if not _has_column(schema, "promotion_variants", "promotion_id"):
        return  # reintento idempotente sobre un tenant ya migrado

    op.alter_column(
        "promotion_variants", "promotion_rule_id",
        existing_type=sa.UUID(), nullable=False, schema=schema,
    )

    op.drop_constraint(
        op.f("uq__promotion_variants__promotion_id__product_variant_id"),
        "promotion_variants", schema=schema, type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq__promotion_variants__promotion_rule_id__product_variant_id"),
        "promotion_variants", ["promotion_rule_id", "product_variant_id"],
        schema=schema,
    )

    op.drop_index(
        op.f("ix__promotion_variants__promotion_id"),
        table_name="promotion_variants", schema=schema,
    )
    op.drop_constraint(
        op.f("fk__promotion_variants__promotion_id__promotions"),
        "promotion_variants", type_="foreignkey", schema=schema,
    )
    op.drop_column("promotion_variants", "promotion_id", schema=schema)

    # `op.f(...)` solo puede invocarse aquí dentro (el proxy de `Operations`
    # no existe al importar el módulo) — no subir esto a nivel de módulo.
    _CK_TYPE = op.f("ck__promotions__ck_promotion_type")
    _CK_VALUE = op.f("ck__promotions__ck_promotion_value_positive")
    _CK_MIN_QTY = op.f("ck__promotions__ck_promotion_min_qty")
    _CK_PERCENT = op.f("ck__promotions__ck_promotion_percent_range")

    op.drop_constraint(_CK_TYPE, "promotions", schema=schema, type_="check")
    op.drop_constraint(_CK_VALUE, "promotions", schema=schema, type_="check")
    op.drop_constraint(_CK_MIN_QTY, "promotions", schema=schema, type_="check")
    op.drop_constraint(_CK_PERCENT, "promotions", schema=schema, type_="check")
    op.drop_column("promotions", "type", schema=schema)
    op.drop_column("promotions", "value", schema=schema)
    op.drop_column("promotions", "min_qty", schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return
    if _has_column(schema, "promotion_variants", "promotion_id"):
        return

    op.add_column(
        "promotions", sa.Column("type", sa.String(50), nullable=True), schema=schema,
    )
    op.add_column(
        "promotions", sa.Column("value", sa.Numeric(12, 2), nullable=True), schema=schema,
    )
    op.add_column(
        "promotions", sa.Column("min_qty", sa.Integer(), nullable=True), schema=schema,
    )

    op.add_column(
        "promotion_variants",
        sa.Column("promotion_id", sa.UUID(), nullable=True),
        schema=schema,
    )

    # --- PASO DE DATOS (repuebla desde promotion_rules antes de endurecer) --- #
    downgrade_flatten_rules_data(op.get_bind(), schema)

    op.create_foreign_key(
        op.f("fk__promotion_variants__promotion_id__promotions"),
        "promotion_variants", "promotions", ["promotion_id"], ["id"],
        source_schema=schema, referent_schema=schema, ondelete="CASCADE",
    )
    op.create_index(
        op.f("ix__promotion_variants__promotion_id"),
        "promotion_variants", ["promotion_id"], schema=schema,
    )
    op.create_unique_constraint(
        op.f("uq__promotion_variants__promotion_id__product_variant_id"),
        "promotion_variants", ["promotion_id", "product_variant_id"],
        schema=schema,
    )
    op.drop_constraint(
        op.f("uq__promotion_variants__promotion_rule_id__product_variant_id"),
        "promotion_variants", schema=schema, type_="unique",
    )
    op.alter_column(
        "promotion_variants", "promotion_rule_id",
        existing_type=sa.UUID(), nullable=True, schema=schema,
    )

    # Ídem `upgrade()`: `op.f(...)` calculado aquí dentro, nunca a nivel de módulo.
    _CK_TYPE = op.f("ck__promotions__ck_promotion_type")
    _CK_VALUE = op.f("ck__promotions__ck_promotion_value_positive")
    _CK_MIN_QTY = op.f("ck__promotions__ck_promotion_min_qty")
    _CK_PERCENT = op.f("ck__promotions__ck_promotion_percent_range")

    op.create_check_constraint(
        _CK_TYPE, "promotions",
        "type IN ('percent', 'package_price') OR status = 'finished'",
        schema=schema,
    )
    op.create_check_constraint(
        _CK_VALUE, "promotions", "value >= 0", schema=schema,
    )
    op.create_check_constraint(
        _CK_MIN_QTY, "promotions", "min_qty >= 1", schema=schema,
    )
    op.create_check_constraint(
        _CK_PERCENT, "promotions", "type <> 'percent' OR value <= 100", schema=schema,
    )
