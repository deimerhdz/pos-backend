"""cart and orders schema (carrito por sesión de mesa y órdenes)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-13 01:00:00.000000

Crea, en cada esquema de tenant, las tablas:
- `cart_items`: items del carrito por sesión de comensal (visibles para toda la mesa).
- `orders` + `order_items`: órdenes de compra generadas desde el carrito (de mesa o individuales),
  con snapshot de nombre/precio de cada producto.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.create_table(
        'cart_items',
        sa.Column('table_id', sa.UUID(), nullable=False),
        sa.Column('table_session_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False, comment='Unique record identifier'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Record creation time'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='Unique record identifier'),
        sa.ForeignKeyConstraint(['table_id'], [f'{schema}.tables.id'], name=op.f('fk__cart_items__table_id__tables')),
        sa.ForeignKeyConstraint(['table_session_id'], [f'{schema}.table_sessions.id'], name=op.f('fk__cart_items__table_session_id__table_sessions')),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__cart_items__product_id__products')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__cart_items')),
        sa.UniqueConstraint('table_session_id', 'product_id', name=op.f('uq__cart_items__table_session_id_product_id')),
        schema=schema,
    )
    op.create_index(op.f('ix__cart_items__table_id'), 'cart_items', ['table_id'], schema=schema)
    op.create_index(op.f('ix__cart_items__table_session_id'), 'cart_items', ['table_session_id'], schema=schema)

    op.create_table(
        'orders',
        sa.Column('table_id', sa.UUID(), nullable=False),
        sa.Column('table_session_id', sa.UUID(), nullable=True),
        sa.Column('scope', sa.String(length=20), nullable=False),
        sa.Column('customer_name', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('total', sa.Numeric(10, 2), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False, comment='Unique record identifier'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Record creation time'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='Unique record identifier'),
        sa.ForeignKeyConstraint(['table_id'], [f'{schema}.tables.id'], name=op.f('fk__orders__table_id__tables')),
        sa.ForeignKeyConstraint(['table_session_id'], [f'{schema}.table_sessions.id'], name=op.f('fk__orders__table_session_id__table_sessions')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__orders')),
        sa.CheckConstraint("scope IN ('individual', 'table')", name=op.f('ck__orders__order_scope')),
        sa.CheckConstraint("status IN ('pending', 'in_progress', 'completed', 'cancelled')", name=op.f('ck__orders__order_status')),
        schema=schema,
    )
    op.create_index(op.f('ix__orders__table_id'), 'orders', ['table_id'], schema=schema)
    op.create_index(op.f('ix__orders__table_session_id'), 'orders', ['table_session_id'], schema=schema)

    op.create_table(
        'order_items',
        sa.Column('order_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('table_session_id', sa.UUID(), nullable=True),
        sa.Column('product_name', sa.String(length=255), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('unit_price', sa.Numeric(10, 2), nullable=False),
        sa.Column('subtotal', sa.Numeric(10, 2), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False, comment='Unique record identifier'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Record creation time'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='Unique record identifier'),
        sa.ForeignKeyConstraint(['order_id'], [f'{schema}.orders.id'], name=op.f('fk__order_items__order_id__orders')),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__order_items__product_id__products')),
        sa.ForeignKeyConstraint(['table_session_id'], [f'{schema}.table_sessions.id'], name=op.f('fk__order_items__table_session_id__table_sessions')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__order_items')),
        schema=schema,
    )
    op.create_index(op.f('ix__order_items__order_id'), 'order_items', ['order_id'], schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix__order_items__order_id'), table_name='order_items', schema=schema)
    op.drop_table('order_items', schema=schema)

    op.drop_index(op.f('ix__orders__table_session_id'), table_name='orders', schema=schema)
    op.drop_index(op.f('ix__orders__table_id'), table_name='orders', schema=schema)
    op.drop_table('orders', schema=schema)

    op.drop_index(op.f('ix__cart_items__table_session_id'), table_name='cart_items', schema=schema)
    op.drop_index(op.f('ix__cart_items__table_id'), table_name='cart_items', schema=schema)
    op.drop_table('cart_items', schema=schema)
