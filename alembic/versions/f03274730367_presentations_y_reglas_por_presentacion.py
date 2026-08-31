"""presentations y reglas por presentacion

Revision ID: f03274730367
Revises: c8ff3a5551cb
Create Date: 2026-08-27 12:03:19.165477

spec 040: catálogo de presentaciones compartido del tenant + tipo de promoción
`qty_price_presentation` con sus reglas en tabla hija. Cero migraciones de datos:
`product_variants.presentation_id` nace NULL para todo el catálogo (FR-008,
compatibilidad hacia atrás, sin backfill). El `downgrade` es simétrico y no
pierde ningún dato histórico — nada histórico se escribe en estas estructuras
(el descuento nunca se persiste, FR-014).
"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'f03274730367'
down_revision: Union[str, Sequence[str], None] = 'c8ff3a5551cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Los esquemas de scratch (tenant_default) pueden no tener las tablas base.
    if not _has_table(schema, "promotions"):
        return

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

    op.create_table(
        "promotion_presentation_rules",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("presentation_id", sa.UUID(), nullable=False),
        sa.Column("min_qty", sa.Integer(), nullable=False),
        sa.Column("pack_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "min_qty >= 1", name=op.f("ck__promotion_presentation_rules__min_qty"),
        ),
        sa.CheckConstraint(
            "pack_price >= 0", name=op.f("ck__promotion_presentation_rules__pack_price"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_presentation_rules__promotion_id__promotions"),
            ondelete="CASCADE",
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
    op.create_index(
        op.f("ix__promotion_presentation_rules__promotion_id"),
        "promotion_presentation_rules", ["promotion_id"], schema=schema,
    )

    op.add_column(
        "product_variants",
        sa.Column("presentation_id", sa.UUID(), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", "presentations",
        ["presentation_id"], ["id"],
        source_schema=schema, referent_schema=schema, ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix__product_variants__presentation_id"),
        "product_variants", ["presentation_id"], schema=schema,
    )

    # `promotions.type` se creó como `varchar(20)` (d3e4f5a6b7c8) — el valor más
    # largo era `qty_price` (9). `qty_price_presentation` tiene 22 caracteres y no
    # cabe: hay que ensanchar la columna antes de permitir el valor nuevo en el
    # CHECK, o el INSERT falla con `StringDataRightTruncation`.
    op.alter_column(
        "promotions", "type",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=False,
        schema=schema,
    )
    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        schema=schema, type_="check",
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price', 'qty_price_presentation')",
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    op.drop_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        schema=schema, type_="check",
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price')",
        schema=schema,
    )
    # Reversa exacta: el CHECK restaurado ya prohíbe `qty_price_presentation`, así
    # que a esta altura no puede quedar ninguna fila con ese valor y estrechar la
    # columna es seguro.
    op.alter_column(
        "promotions", "type",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=False,
        schema=schema,
    )

    op.drop_index(
        op.f("ix__product_variants__presentation_id"),
        table_name="product_variants", schema=schema,
    )
    op.drop_constraint(
        op.f("fk__product_variants__presentation_id__presentations"),
        "product_variants", type_="foreignkey", schema=schema,
    )
    op.drop_column("product_variants", "presentation_id", schema=schema)

    op.drop_index(
        op.f("ix__promotion_presentation_rules__promotion_id"),
        table_name="promotion_presentation_rules", schema=schema,
    )
    op.drop_table("promotion_presentation_rules", schema=schema)
    op.drop_table("presentations", schema=schema)
