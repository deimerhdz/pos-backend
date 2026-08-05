"""combos de promociones, dias_del_mes, combo_id en items

Revision ID: a63ddb1f0d97
Revises: f2a3b4c5d6e7
Create Date: 2026-08-03 12:14:52.784379

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'a63ddb1f0d97'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Los esquemas de scratch (tenant_default) pueden no tener las tablas base.
    if not _has_table(schema, "sales"):
        return

    op.add_column(
        "promotions", sa.Column("days_of_month", sa.String(length=100), nullable=True),
        schema=schema,
    )

    op.create_table(
        "promotion_combo_items",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("product_variant_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck__promotion_combo_items__ck_promotion_combo_item_qty_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], [f"{schema}.promotions.id"],
            name=op.f("fk__promotion_combo_items__promotion_id__promotions"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], [f"{schema}.product_variants.id"],
            name=op.f("fk__promotion_combo_items__product_variant_id__product_variants"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotion_combo_items")),
        sa.UniqueConstraint(
            "promotion_id", "product_variant_id",
            name="uq__promotion_combo_items__promotion_id__product_variant_id",
        ),
        schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_combo_items__promotion_id"), "promotion_combo_items",
        ["promotion_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_combo_items__product_variant_id"), "promotion_combo_items",
        ["product_variant_id"], schema=schema,
    )

    for table in ("cart_items", "order_items", "sale_items"):
        op.add_column(table, sa.Column("combo_id", sa.UUID(), nullable=True), schema=schema)
        op.create_foreign_key(
            op.f(f"fk__{table}__combo_id__promotions"), table, "promotions",
            ["combo_id"], ["id"], source_schema=schema, referent_schema=schema, ondelete="SET NULL",
        )
        op.create_index(
            op.f(f"ix__{table}__combo_id"), table, ["combo_id"], schema=schema,
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "sales"):
        return

    for table in ("sale_items", "order_items", "cart_items"):
        op.drop_index(op.f(f"ix__{table}__combo_id"), table_name=table, schema=schema)
        op.drop_constraint(op.f(f"fk__{table}__combo_id__promotions"), table, type_="foreignkey", schema=schema)
        op.drop_column(table, "combo_id", schema=schema)

    op.drop_index(op.f("ix__promotion_combo_items__product_variant_id"), table_name="promotion_combo_items", schema=schema)
    op.drop_index(op.f("ix__promotion_combo_items__promotion_id"), table_name="promotion_combo_items", schema=schema)
    op.drop_table("promotion_combo_items", schema=schema)

    op.drop_column("promotions", "days_of_month", schema=schema)
