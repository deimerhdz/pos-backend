"""063a promociones por conjunto aditivo

Revision ID: 387ef3e638cd
Revises: e1c455751dbc
Create Date: 2026-08-31 15:10:00.194618

spec 063 — Incremento A (100% aditivo). Ver
`specs/063-promociones-por-variante/data-model.md` §"Migración y rollback" y
`contracts/migracion.md` §1. Decisión de negocio: A-60/A-62/A-64
(`specs/000-reconocimiento/registro-de-anomalias.md`).

Esta revisión NO borra ninguna estructura vieja: `promotion_targets`,
`promotion_combo_items`, `promotion_presentation_rules`, `presentations`,
`promotions.priority` y `product_variants.presentation_id` siguen intactas y el
motor viejo sigue corriendo. El retiro de todo eso va en la revisión destructiva
`063b` (Incremento F), cuando ningún módulo lo referencia ya.

Cambios:
- tabla nueva `promotion_variants` (hija de `promotions`, sin atributos de precio);
- `promotions.closed_by_refactor_at` (marca de "finalizada por la migración", FR-025);
- `sales.applied_promotions` / `invoices.applied_promotions` (JSONB, snapshot, FR-021);
- `customer_orders.discount` + `.applied_promotions` + CHECK `discount >= 0` (FR-021);
- CHECK `ck_promotion_min_qty` (`min_qty >= 1`);
- `ck_promotion_type` AMPLIADO con `package_price` (no se quita ningún valor viejo);
- PASO DE DATOS (`migrate_promotions_data`): cada `percent` materializa su conjunto
  de variantes activas (foto fija, FR-026); cada `combo`/`fixed`/`qty_price`/
  `qty_price_presentation` no terminal pasa a `status='finished'` +
  `closed_by_refactor_at` (FR-025). El `type` no cambia (registro histórico).

El `downgrade` revierte solo lo aditivo; el paso de datos NO se revierte (las
promociones `finished` no se reactivan, las filas de `promotion_variants` se
pierden) — documentado en data-model.md §Rollback. Ninguna `Sale`/`Invoice`
emitida cambia de importe en ningún sentido (Principio VII).
"""
from datetime import datetime, timezone
from typing import Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.scripts.tenant import for_each_tenant_schema


# revision identifiers, used by Alembic.
revision: str = '387ef3e638cd'
down_revision: Union[str, Sequence[str], None] = 'e1c455751dbc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


# --------------------------------------------------------------------------- #
# Paso de datos — función pura, testeable sin PostgreSQL (T006 / T050).
#
# `schema=None` -> tablas sin cualificar (SQLite en memoria de los
# characterization tests, con `schema_translate_map={"tenant": None}`).
# `schema="<tenant>"` -> tablas cualificadas por schema (migración real).
# Usa Core con columnas tipadas `UUID(as_uuid=True)` — el mismo tipo del ORM —
# para que el bind de UUID funcione igual en Postgres y en SQLite.
# --------------------------------------------------------------------------- #
def _data_tables(schema: Optional[str]) -> dict:
    md = sa.MetaData()
    return {
        "promotions": sa.Table(
            "promotions", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("type", sa.String(50)),
            sa.Column("status", sa.String(16)),
            sa.Column("closed_by_refactor_at", sa.DateTime),
            schema=schema,
        ),
        "promotion_targets": sa.Table(
            "promotion_targets", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", UUID(as_uuid=True)),
            sa.Column("product_id", UUID(as_uuid=True)),
            sa.Column("category_id", UUID(as_uuid=True)),
            schema=schema,
        ),
        "products": sa.Table(
            "products", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("category_id", UUID(as_uuid=True)),
            schema=schema,
        ),
        "product_variants": sa.Table(
            "product_variants", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("product_id", UUID(as_uuid=True)),
            sa.Column("active", sa.Boolean),
            schema=schema,
        ),
        "promotion_variants": sa.Table(
            "promotion_variants", md,
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", UUID(as_uuid=True)),
            sa.Column("product_variant_id", UUID(as_uuid=True)),
            schema=schema,
        ),
    }


# FR-025: tipos legados no terminales que la migración cierra.
_CLOSED_BY_REFACTOR_TYPES = ("combo", "fixed", "qty_price", "qty_price_presentation")


def migrate_promotions_data(bind, schema: Optional[str] = None) -> None:
    """Paso de datos de `063a` (data-model.md §Migración `063a`, research.md D12).

    (a) por cada `promotions.type = 'percent'`: materializa en `promotion_variants`
        las `product_variants.active` alcanzadas por sus `promotion_targets` de
        producto/categoría, o **todas** las activas del tenant si no tiene targets
        (percent global). Foto fija (FR-026).
    (b) `combo`/`fixed`/`qty_price`/`qty_price_presentation` no terminales ->
        `status='finished'` + `closed_by_refactor_at` (FR-025). El `type` no cambia.
    """
    import uuid as _uuid

    t = _data_tables(schema)
    promotions = t["promotions"]
    promotion_targets = t["promotion_targets"]
    products = t["products"]
    product_variants = t["product_variants"]
    promotion_variants = t["promotion_variants"]

    # (a) percent -> conjunto de variantes (foto fija, FR-026)
    percent_ids = [
        row[0]
        for row in bind.execute(
            sa.select(promotions.c.id).where(promotions.c.type == "percent")
        )
    ]
    for promo_id in percent_ids:
        targets = bind.execute(
            sa.select(
                promotion_targets.c.product_id, promotion_targets.c.category_id
            ).where(promotion_targets.c.promotion_id == promo_id)
        ).fetchall()

        product_ids = [r[0] for r in targets if r[0] is not None]
        category_ids = [r[1] for r in targets if r[1] is not None]

        variant_ids: set = set()
        if not targets:
            # percent global -> todas las variantes activas del tenant
            variant_ids.update(
                row[0]
                for row in bind.execute(
                    sa.select(product_variants.c.id).where(product_variants.c.active)
                )
            )
        else:
            if product_ids:
                variant_ids.update(
                    row[0]
                    for row in bind.execute(
                        sa.select(product_variants.c.id).where(
                            product_variants.c.active,
                            product_variants.c.product_id.in_(product_ids),
                        )
                    )
                )
            if category_ids:
                variant_ids.update(
                    row[0]
                    for row in bind.execute(
                        sa.select(product_variants.c.id)
                        .select_from(
                            product_variants.join(
                                products,
                                products.c.id == product_variants.c.product_id,
                            )
                        )
                        .where(
                            product_variants.c.active,
                            products.c.category_id.in_(category_ids),
                        )
                    )
                )

        if variant_ids:
            bind.execute(
                sa.insert(promotion_variants),
                [
                    {
                        "id": _uuid.uuid4(),
                        "promotion_id": promo_id,
                        "product_variant_id": vid,
                    }
                    for vid in variant_ids
                ],
            )

    # (b) combo / fixed / qty_price / qty_price_presentation no terminales -> Finalizada
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    bind.execute(
        sa.update(promotions)
        .where(
            promotions.c.type.in_(_CLOSED_BY_REFACTOR_TYPES),
            promotions.c.status != "finished",
        )
        .values(status="finished", closed_by_refactor_at=now_naive)
    )


# --------------------------------------------------------------------------- #
# Migración
# --------------------------------------------------------------------------- #
@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    # --- 1. estructura nueva (compatible hacia atrás) --- #
    op.create_table(
        "promotion_variants",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("product_variant_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_variants__promotion_id__promotions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], [f"{schema}.product_variants.id"],
            name=op.f("fk__promotion_variants__product_variant_id__product_variants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_variants")),
        sa.UniqueConstraint(
            "promotion_id", "product_variant_id",
            name="uq__promotion_variants__promotion_id__product_variant_id",
        ),
        schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_variants__promotion_id"),
        "promotion_variants", ["promotion_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_variants__product_variant_id"),
        "promotion_variants", ["product_variant_id"], schema=schema,
    )

    op.add_column(
        "promotions",
        sa.Column("closed_by_refactor_at", sa.DateTime(), nullable=True),
        schema=schema,
    )

    op.add_column(
        "sales",
        sa.Column(
            "applied_promotions", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=schema,
    )
    op.add_column(
        "invoices",
        sa.Column(
            "applied_promotions", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=schema,
    )
    op.add_column(
        "customer_orders",
        sa.Column(
            "discount", sa.Numeric(12, 2), nullable=False, server_default="0",
        ),
        schema=schema,
    )
    op.add_column(
        "customer_orders",
        sa.Column(
            "applied_promotions", JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=schema,
    )
    op.create_check_constraint(
        op.f("ck__customer_orders__ck_customer_order_discount_non_negative"),
        "customer_orders", "discount >= 0", schema=schema,
    )

    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_min_qty"),
        "promotions", "min_qty >= 1", schema=schema,
    )

    # `ck_promotion_type` AMPLIADO: se agrega 'package_price'; NO se quita ningún
    # valor viejo todavía (eso es `063b`).
    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        schema=schema, type_="check",
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price', "
        "'qty_price_presentation', 'package_price')",
        schema=schema,
    )

    # --- 2. PASO DE DATOS (con targets/combo/presentation TODAVÍA presentes) --- #
    migrate_promotions_data(op.get_bind(), schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    # El paso de datos NO se revierte (data-model.md §Rollback): las promociones
    # 'finished' no se reactivan; las filas de `promotion_variants` se pierden
    # al hacer drop de la tabla. `promotion_targets` etc. siguen intactas
    # (nunca se tocaron aquí).
    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        schema=schema, type_="check",
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price', 'qty_price_presentation')",
        schema=schema,
    )

    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_min_qty"), "promotions",
        schema=schema, type_="check",
    )

    op.drop_constraint(
        op.f("ck__customer_orders__ck_customer_order_discount_non_negative"),
        "customer_orders", schema=schema, type_="check",
    )
    op.drop_column("customer_orders", "applied_promotions", schema=schema)
    op.drop_column("customer_orders", "discount", schema=schema)
    op.drop_column("invoices", "applied_promotions", schema=schema)
    op.drop_column("sales", "applied_promotions", schema=schema)
    op.drop_column("promotions", "closed_by_refactor_at", schema=schema)

    op.drop_index(
        op.f("ix__promotion_variants__product_variant_id"),
        table_name="promotion_variants", schema=schema,
    )
    op.drop_index(
        op.f("ix__promotion_variants__promotion_id"),
        table_name="promotion_variants", schema=schema,
    )
    op.drop_table("promotion_variants", schema=schema)
