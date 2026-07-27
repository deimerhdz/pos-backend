"""promociones: promotions, promotion_targets, sales.promotion_id

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
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

    op.create_table(
        "promotions",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("days_of_week", sa.String(length=20), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("min_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("buy_qty", sa.Integer(), nullable=True),
        sa.Column("get_qty", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "type IN ('percent', 'fixed', 'buy_x_get_y', 'combo', 'qty_price')",
            name=op.f("ck__promotions__ck_promotion_type"),
        ),
        sa.CheckConstraint("value >= 0", name=op.f("ck__promotions__ck_promotion_value_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__promotions")),
        sa.UniqueConstraint("name", name=op.f("uq__promotions__name")),
        schema=schema,
    )

    op.create_table(
        "promotion_targets",
        sa.Column("promotion_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "(product_id IS NOT NULL) OR (category_id IS NOT NULL)",
            name=op.f("ck__promotion_targets__ck_promotion_target_scope"),
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
    op.create_index(
        op.f("ix__promotion_targets__promotion_id"), "promotion_targets",
        ["promotion_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_targets__product_id"), "promotion_targets",
        ["product_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__promotion_targets__category_id"), "promotion_targets",
        ["category_id"], schema=schema,
    )

    op.add_column("sales", sa.Column("promotion_id", sa.UUID(), nullable=True), schema=schema)
    op.create_foreign_key(
        op.f("fk__sales__promotion_id__promotions"), "sales", "promotions",
        ["promotion_id"], ["id"], source_schema=schema, referent_schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "sales"):
        return
    op.drop_constraint(op.f("fk__sales__promotion_id__promotions"), "sales",
                       type_="foreignkey", schema=schema)
    op.drop_column("sales", "promotion_id", schema=schema)
    op.drop_table("promotion_targets", schema=schema)
    op.drop_table("promotions", schema=schema)
