"""fase3 cutover POS (variante+modificadores en carrito/orden, impuestos)

Revision ID: b2c3f3d4e5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-07-10 14:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3f3d4e5a6'
down_revision: Union[str, Sequence[str], None] = 'a9b8c7d6e5f4'
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
    if not _has(schema, "variants"):
        return
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    sq = preparer.format_schema(schema)

    # --- cart_items: variante + product_id nullable, quitar unique legacy ---
    op.add_column('cart_items', sa.Column('variant_id', sa.UUID(), nullable=True), schema=schema)
    op.create_foreign_key(op.f('fk__cart_items__variant_id__variants'), 'cart_items', 'variants',
                          ['variant_id'], ['id'], source_schema=schema, referent_schema=schema)
    op.create_index(op.f('ix__cart_items__variant_id'), 'cart_items', ['variant_id'], schema=schema)
    op.alter_column('cart_items', 'product_id', existing_type=sa.UUID(), nullable=True, schema=schema)
    op.execute(sa.text(f'ALTER TABLE {sq}.cart_items DROP CONSTRAINT IF EXISTS uq__cart_items__table_session_id_product_id'))

    # --- cart_item_modifiers ---
    op.create_table(
        'cart_item_modifiers',
        sa.Column('cart_item_id', sa.UUID(), nullable=False),
        sa.Column('modifier_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['cart_item_id'], [f'{schema}.cart_items.id'], name=op.f('fk__cart_item_modifiers__cart_item_id__cart_items')),
        sa.ForeignKeyConstraint(['modifier_id'], [f'{schema}.modifiers.id'], name=op.f('fk__cart_item_modifiers__modifier_id__modifiers')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__cart_item_modifiers')),
        schema=schema,
    )
    op.create_index(op.f('ix__cart_item_modifiers__cart_item_id'), 'cart_item_modifiers', ['cart_item_id'], schema=schema)

    # --- order_items: variante + tax_amount, product_id nullable ---
    op.add_column('order_items', sa.Column('variant_id', sa.UUID(), nullable=True), schema=schema)
    op.create_foreign_key(op.f('fk__order_items__variant_id__variants'), 'order_items', 'variants',
                          ['variant_id'], ['id'], source_schema=schema, referent_schema=schema)
    op.add_column('order_items', sa.Column('tax_amount', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    op.alter_column('order_items', 'product_id', existing_type=sa.UUID(), nullable=True, schema=schema)

    # --- order_item_modifiers ---
    op.create_table(
        'order_item_modifiers',
        sa.Column('order_item_id', sa.UUID(), nullable=False),
        sa.Column('modifier_id', sa.UUID(), nullable=True),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['order_item_id'], [f'{schema}.order_items.id'], name=op.f('fk__order_item_modifiers__order_item_id__order_items')),
        sa.ForeignKeyConstraint(['modifier_id'], [f'{schema}.modifiers.id'], name=op.f('fk__order_item_modifiers__modifier_id__modifiers')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__order_item_modifiers')),
        schema=schema,
    )
    op.create_index(op.f('ix__order_item_modifiers__order_item_id'), 'order_item_modifiers', ['order_item_id'], schema=schema)

    # --- orders: subtotal + tax_total ---
    op.add_column('orders', sa.Column('subtotal', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    op.add_column('orders', sa.Column('tax_total', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    op.execute(sa.text(f'UPDATE {sq}.orders SET subtotal = total'))

    # --- variante default para productos SIMPLE sin variante (para poder venderlos) ---
    op.execute(sa.text(f"""
        INSERT INTO {sq}.variants (id, product_id, sku, price, is_default, active, created_at)
        SELECT gen_random_uuid(), p.id,
               'DEF-' || substr(replace(p.id::text, '-', ''), 1, 12),
               COALESCE(p.price, 0), true, true, now()
        FROM {sq}.products p
        WHERE p.type = 'SIMPLE'
          AND NOT EXISTS (SELECT 1 FROM {sq}.variants v WHERE v.product_id = p.id)
    """))


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has(schema, "order_item_modifiers"):
        return

    op.drop_table('order_item_modifiers', schema=schema)
    op.drop_table('cart_item_modifiers', schema=schema)

    op.drop_column('orders', 'tax_total', schema=schema)
    op.drop_column('orders', 'subtotal', schema=schema)

    op.drop_constraint(op.f('fk__order_items__variant_id__variants'), 'order_items', schema=schema, type_='foreignkey')
    op.drop_column('order_items', 'tax_amount', schema=schema)
    op.drop_column('order_items', 'variant_id', schema=schema)

    op.drop_constraint(op.f('fk__cart_items__variant_id__variants'), 'cart_items', schema=schema, type_='foreignkey')
    op.drop_index(op.f('ix__cart_items__variant_id'), 'cart_items', schema=schema)
    op.drop_column('cart_items', 'variant_id', schema=schema)
