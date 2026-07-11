"""refactor fase2: stock_reservations (reserva al pedir, consumo al cobrar)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-10 17:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.{table}"}).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not (_has(schema, "order_items") and _has(schema, "supplies")):
        return
    op.create_table(
        'stock_reservations',
        sa.Column('order_id', sa.UUID(), nullable=False),
        sa.Column('order_item_id', sa.UUID(), nullable=False),
        sa.Column('supply_id', sa.UUID(), nullable=False),
        sa.Column('quantity_reserved', sa.Numeric(14, 3), nullable=False),
        sa.Column('status', sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], [f'{schema}.orders.id'], name=op.f('fk__stock_reservations__order_id__orders')),
        sa.ForeignKeyConstraint(['order_item_id'], [f'{schema}.order_items.id'], name=op.f('fk__stock_reservations__order_item_id__order_items')),
        sa.ForeignKeyConstraint(['supply_id'], [f'{schema}.supplies.id'], name=op.f('fk__stock_reservations__supply_id__supplies')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__stock_reservations')),
        sa.CheckConstraint("status IN ('active', 'released', 'consumed')", name='ck_reservation_status'),
        schema=schema,
    )
    op.create_index(op.f('ix__stock_reservations__order_id'), 'stock_reservations', ['order_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__stock_reservations__supply_id'), 'stock_reservations', ['supply_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__stock_reservations__expires_at'), 'stock_reservations', ['expires_at'], unique=False, schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has(schema, "stock_reservations"):
        return
    op.drop_table('stock_reservations', schema=schema)
