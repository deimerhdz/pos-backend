"""cleanup legacy: drop inventory, inventory_movements, product_component + product columns

Revision ID: c3d4f4e5a6b7
Revises: b2c3f3d4e5a6
Create Date: 2026-07-10 15:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4f4e5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b2c3f3d4e5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _std_cols():
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    ]


def _has(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.{table}"}).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has(schema, "products"):
        return
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    sq = preparer.format_schema(schema)

    for table in ("product_component", "inventory_movements", "inventory"):
        if _has(schema, table):
            op.drop_table(table, schema=schema)

    op.execute(sa.text(f"ALTER TABLE {sq}.products DROP CONSTRAINT IF EXISTS ck_product_type"))
    op.execute(sa.text(f"ALTER TABLE {sq}.products DROP COLUMN IF EXISTS control_stock"))
    op.execute(sa.text(f"ALTER TABLE {sq}.products DROP COLUMN IF EXISTS product_type"))


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has(schema, "products"):
        return

    op.add_column('products', sa.Column('product_type', sa.String(50), server_default=sa.text("'PRODUCT'"), nullable=False), schema=schema)
    op.add_column('products', sa.Column('control_stock', sa.Boolean(), server_default=sa.text('false'), nullable=False), schema=schema)
    op.create_check_constraint('ck_product_type', 'products', "product_type IN ('INGREDIENT', 'PRODUCT', 'RECIPE')", schema=schema)

    op.create_table(
        'inventory',
        sa.Column('stock', sa.Integer(), nullable=False),
        sa.Column('stock_min', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__inventory__product_id__products')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__inventory')),
        schema=schema,
    )
    op.create_table(
        'inventory_movements',
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('stock_before', sa.Integer(), nullable=False),
        sa.Column('stock_after', sa.Integer(), nullable=False),
        sa.Column('type_movement', sa.String(50), nullable=False),
        sa.Column('reference_id', sa.UUID(), nullable=True),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('reason', sa.String(255), nullable=True),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__inventory_movements__product_id__products')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__inventory_movements')),
        sa.CheckConstraint("type_movement IN ('income', 'expense')", name='ck_type_movement'),
        schema=schema,
    )
    op.create_table(
        'product_component',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('component_id', sa.UUID(), nullable=False),
        sa.Column('quantity', sa.Numeric(12, 3), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__product_component__product_id__products')),
        sa.ForeignKeyConstraint(['component_id'], [f'{schema}.products.id'], name=op.f('fk__product_component__component_id__products')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__product_component')),
        sa.UniqueConstraint('product_id', 'component_id', name=op.f('uq__product_component__product_id_component_id')),
        schema=schema,
    )
