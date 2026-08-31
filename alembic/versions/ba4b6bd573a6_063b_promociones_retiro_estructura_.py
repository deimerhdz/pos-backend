"""063b promociones retiro estructura legada

Revision ID: ba4b6bd573a6
Revises: 387ef3e638cd
Create Date: 2026-08-31 16:35:35.525558

spec 063 — Incremento F (revisión **destructiva**). Ver
`specs/063-promociones-por-variante/data-model.md` §"Revisión `063b`" y
`contracts/migracion.md` §1. Decisión de negocio: A-58 / A-60 / A-61 / A-62 /
A-63 (`specs/000-reconocimiento/registro-de-anomalias.md`).

Se aplica **solo cuando ningún módulo referencia ya la estructura vieja** (hasta
la Phase 8 `menu/router.py` importaba `Presentation`). Borra:
- `promotion_presentation_rules`, `promotion_combo_items`, `promotion_targets`;
- `product_variants.presentation_id` (+ FK + índice);
- `presentations`;
- `promotions.priority`;
- `ck_promotion_qty_price_pack` (constraint muerto: `qty_price` ya no nace);
- `ck_promotion_type` ESTRECHADO **con escape** a
  `type IN ('percent','package_price') OR status = 'finished'`.

**No toca ninguna `Sale` / `Invoice` emitida** (Principio VII). El `downgrade`
recrea la estructura de spec 013/040 **vacía** y sin reversa del paso de datos
(documentado en data-model.md §Rollback).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema


# revision identifiers, used by Alembic.
revision: str = 'ba4b6bd573a6'
down_revision: Union[str, Sequence[str], None] = '387ef3e638cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK_TYPE = op.f("ck__promotions__ck_promotion_type")


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    # --- borrado de lo que el modelo nuevo deja sin sentido ---
    op.drop_table("promotion_presentation_rules", schema=schema)
    op.drop_table("promotion_combo_items", schema=schema)
    op.drop_table("promotion_targets", schema=schema)

    op.drop_index(
        op.f("ix__product_variants__presentation_id"),
        table_name="product_variants", schema=schema,
    )
    op.drop_constraint(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", type_="foreignkey", schema=schema,
    )
    op.drop_column("product_variants", "presentation_id", schema=schema)

    op.drop_table("presentations", schema=schema)

    op.drop_column("promotions", "priority", schema=schema)

    # --- CHECKs ---
    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_qty_price_pack"), "promotions",
        schema=schema, type_="check",
    )
    op.drop_constraint(_CK_TYPE, "promotions", schema=schema, type_="check")
    op.create_check_constraint(
        _CK_TYPE, "promotions",
        "type IN ('percent', 'package_price') OR status = 'finished'",
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    # --- CHECKs (estructura de spec 040) ---
    op.drop_constraint(_CK_TYPE, "promotions", schema=schema, type_="check")
    op.create_check_constraint(
        _CK_TYPE, "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price', 'qty_price_presentation')",
        schema=schema,
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_qty_price_pack"), "promotions",
        "type <> 'qty_price' OR min_qty >= 2", schema=schema,
    )

    op.add_column(
        "promotions",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )

    # --- presentations (spec 040, vacía) ---
    op.create_table(
        "presentations",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__presentations")),
        sa.UniqueConstraint("name", name="uq__presentations__name"),
        schema=schema,
    )

    op.add_column(
        "product_variants", sa.Column("presentation_id", sa.UUID(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", "presentations", ["presentation_id"], ["id"],
        source_schema=schema, referent_schema=schema, ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix__product_variants__presentation_id"),
        "product_variants", ["presentation_id"], schema=schema,
    )

    # --- promotion_targets (spec 013/040, vacía) ---
    op.create_table(
        "promotion_targets",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("value", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_qty", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(product_id IS NOT NULL) OR (category_id IS NOT NULL)",
            name=op.f("ck__promotion_targets__ck_promotion_target_scope"),
        ),
        sa.CheckConstraint(
            "value IS NULL OR value >= 0",
            name=op.f("ck__promotion_targets__ck_target_value_positive"),
        ),
        sa.CheckConstraint(
            "min_qty IS NULL OR min_qty >= 2",
            name=op.f("ck__promotion_targets__ck_target_pack_size"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_targets__promotion_id__promotions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{schema}.products.id"],
            name=op.f("fk__promotion_targets__product_id__products"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], [f"{schema}.categories.id"],
            name=op.f("fk__promotion_targets__category_id__categories"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_targets")),
        schema=schema,
    )
    op.create_index(op.f("ix__promotion_targets__promotion_id"), "promotion_targets",
                    ["promotion_id"], schema=schema)

    # --- promotion_combo_items (spec 013, vacía) ---
    op.create_table(
        "promotion_combo_items",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("product_variant_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name=op.f("ck__promotion_combo_items__ck_promotion_combo_item_qty_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_combo_items__promotion_id__promotions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], [f"{schema}.product_variants.id"],
            name=op.f("fk__promotion_combo_items__product_variant_id__product_variants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_combo_items")),
        sa.UniqueConstraint(
            "promotion_id", "product_variant_id",
            name="uq__promotion_combo_items__promotion_id__product_variant_id",
        ),
        schema=schema,
    )
    op.create_index(op.f("ix__promotion_combo_items__promotion_id"), "promotion_combo_items",
                    ["promotion_id"], schema=schema)

    # --- promotion_presentation_rules (spec 040, vacía) ---
    op.create_table(
        "promotion_presentation_rules",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("presentation_id", sa.UUID(), nullable=False),
        sa.Column("min_qty", sa.Integer(), nullable=False),
        sa.Column("pack_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint("min_qty >= 1", name=op.f("ck__promotion_presentation_rules__min_qty")),
        sa.CheckConstraint("pack_price >= 0", name=op.f("ck__promotion_presentation_rules__pack_price")),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_presentation_rules__promotion_id__promotions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"], [f"{schema}.presentations.id"],
            name=op.f("fk__promotion_presentation_rules__presentation_id__presentations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_presentation_rules")),
        sa.UniqueConstraint(
            "promotion_id", "presentation_id",
            name="uq__promotion_presentation_rules__promotion_id__presentation_id",
        ),
        schema=schema,
    )
    op.create_index(op.f("ix__promotion_presentation_rules__promotion_id"),
                    "promotion_presentation_rules", ["promotion_id"], schema=schema)
