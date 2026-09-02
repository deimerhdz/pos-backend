"""option_groups.selection_mode + topes de cantidad; cart_item_options/order_item_options.quantity (spec 065: selección por cantidad en grupos de opciones)

Revision ID: a96852d7be6a
Revises: 68326ed66ebf
Create Date: 2026-09-01 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'a96852d7be6a'
down_revision: Union[str, Sequence[str], None] = '68326ed66ebf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if _has_table(schema, "option_groups"):
        op.add_column(
            "option_groups",
            sa.Column("selection_mode", sa.String(length=20), nullable=False, server_default="conteo"),
            schema=schema,
        )
        op.add_column(
            "option_groups",
            sa.Column("max_quantity_per_option", sa.Integer(), nullable=True),
            schema=schema,
        )
        op.add_column(
            "option_groups",
            sa.Column("max_total_quantity", sa.Integer(), nullable=True),
            schema=schema,
        )
        op.create_check_constraint(
            "ck_option_group_selection_mode",
            "option_groups",
            "selection_mode IN ('conteo', 'cantidad')",
            schema=schema,
        )
        op.create_check_constraint(
            "ck_option_group_max_quantity_per_option",
            "option_groups",
            "max_quantity_per_option IS NULL OR max_quantity_per_option > 0",
            schema=schema,
        )
        op.create_check_constraint(
            "ck_option_group_max_total_quantity",
            "option_groups",
            "max_total_quantity IS NULL OR max_total_quantity > 0",
            schema=schema,
        )

    if _has_table(schema, "cart_item_options"):
        op.add_column(
            "cart_item_options",
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            schema=schema,
        )
        op.create_check_constraint(
            "ck_cart_item_options_quantity_positive",
            "cart_item_options",
            "quantity > 0",
            schema=schema,
        )

    if _has_table(schema, "order_item_options"):
        op.add_column(
            "order_item_options",
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            schema=schema,
        )
        op.create_check_constraint(
            "ck_order_item_options_quantity_positive",
            "order_item_options",
            "quantity > 0",
            schema=schema,
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if _has_table(schema, "order_item_options"):
        op.drop_constraint(
            op.f("ck__order_item_options__ck_order_item_options_quantity_positive"),
            "order_item_options", schema=schema, type_="check",
        )
        op.drop_column("order_item_options", "quantity", schema=schema)

    if _has_table(schema, "cart_item_options"):
        op.drop_constraint(
            op.f("ck__cart_item_options__ck_cart_item_options_quantity_positive"),
            "cart_item_options", schema=schema, type_="check",
        )
        op.drop_column("cart_item_options", "quantity", schema=schema)

    if _has_table(schema, "option_groups"):
        op.drop_constraint(
            op.f("ck__option_groups__ck_option_group_max_total_quantity"),
            "option_groups", schema=schema, type_="check",
        )
        op.drop_constraint(
            op.f("ck__option_groups__ck_option_group_max_quantity_per_option"),
            "option_groups", schema=schema, type_="check",
        )
        op.drop_constraint(
            op.f("ck__option_groups__ck_option_group_selection_mode"),
            "option_groups", schema=schema, type_="check",
        )
        op.drop_column("option_groups", "max_total_quantity", schema=schema)
        op.drop_column("option_groups", "max_quantity_per_option", schema=schema)
        op.drop_column("option_groups", "selection_mode", schema=schema)
