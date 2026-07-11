"""fase2 insumos, recetas y consumo (supplies, batches, recipes, movements)

Revision ID: a9b8c7d6e5f4
Revises: f7a8b9c0d1e2
Create Date: 2026-07-10 13:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, Sequence[str], None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _std_cols():
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    ]


def _has_phase1_tables(schema: str) -> bool:
    bind = op.get_bind()
    return bind.execute(
        sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.variants"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_phase1_tables(schema):
        return

    # --- supplies ---
    op.create_table(
        'supplies',
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('unit_measure_id', sa.UUID(), nullable=False),
        sa.Column('stock_current', sa.Numeric(14, 3), server_default=sa.text('0'), nullable=False),
        sa.Column('stock_min', sa.Numeric(14, 3), server_default=sa.text('0'), nullable=False),
        sa.Column('track_expiry', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['unit_measure_id'], [f'{schema}.unit_measures.id'], name=op.f('fk__supplies__unit_measure_id__unit_measures')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__supplies')),
        schema=schema,
    )
    op.create_index(op.f('ix__supplies__name'), 'supplies', ['name'], unique=False, schema=schema)

    # --- supply_batches ---
    op.create_table(
        'supply_batches',
        sa.Column('supply_id', sa.UUID(), nullable=False),
        sa.Column('code', sa.String(100), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('expires_at', sa.Date(), nullable=True),
        sa.Column('unit_cost', sa.Numeric(12, 4), server_default=sa.text('0'), nullable=False),
        sa.Column('received_at', sa.Date(), server_default=sa.text('now()'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['supply_id'], [f'{schema}.supplies.id'], name=op.f('fk__supply_batches__supply_id__supplies')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__supply_batches')),
        schema=schema,
    )
    op.create_index(op.f('ix__supply_batches__supply_id'), 'supply_batches', ['supply_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__supply_batches__expires_at'), 'supply_batches', ['expires_at'], unique=False, schema=schema)

    # --- recipes ---
    op.create_table(
        'recipes',
        sa.Column('variant_id', sa.UUID(), nullable=True),
        sa.Column('modifier_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['variant_id'], [f'{schema}.variants.id'], name=op.f('fk__recipes__variant_id__variants')),
        sa.ForeignKeyConstraint(['modifier_id'], [f'{schema}.modifiers.id'], name=op.f('fk__recipes__modifier_id__modifiers')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__recipes')),
        sa.CheckConstraint('(variant_id IS NOT NULL)::int + (modifier_id IS NOT NULL)::int = 1', name='ck_recipe_single_owner'),
        sa.UniqueConstraint('variant_id', name='uq__recipes__variant_id'),
        sa.UniqueConstraint('modifier_id', name='uq__recipes__modifier_id'),
        schema=schema,
    )

    # --- recipe_items ---
    op.create_table(
        'recipe_items',
        sa.Column('recipe_id', sa.UUID(), nullable=False),
        sa.Column('supply_id', sa.UUID(), nullable=False),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('unit_measure_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['recipe_id'], [f'{schema}.recipes.id'], name=op.f('fk__recipe_items__recipe_id__recipes')),
        sa.ForeignKeyConstraint(['supply_id'], [f'{schema}.supplies.id'], name=op.f('fk__recipe_items__supply_id__supplies')),
        sa.ForeignKeyConstraint(['unit_measure_id'], [f'{schema}.unit_measures.id'], name=op.f('fk__recipe_items__unit_measure_id__unit_measures')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__recipe_items')),
        schema=schema,
    )
    op.create_index(op.f('ix__recipe_items__recipe_id'), 'recipe_items', ['recipe_id'], unique=False, schema=schema)

    # --- supply_movements ---
    op.create_table(
        'supply_movements',
        sa.Column('supply_id', sa.UUID(), nullable=False),
        sa.Column('batch_id', sa.UUID(), nullable=True),
        sa.Column('quantity', sa.Numeric(14, 3), nullable=False),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('reference_id', sa.UUID(), nullable=True),
        sa.Column('reason', sa.String(255), nullable=True),
        *_std_cols(),
        sa.ForeignKeyConstraint(['supply_id'], [f'{schema}.supplies.id'], name=op.f('fk__supply_movements__supply_id__supplies')),
        sa.ForeignKeyConstraint(['batch_id'], [f'{schema}.supply_batches.id'], name=op.f('fk__supply_movements__batch_id__supply_batches')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__supply_movements')),
        sa.CheckConstraint("type IN ('income', 'expense', 'adjust', 'waste')", name='ck_supply_movement_type'),
        schema=schema,
    )
    op.create_index(op.f('ix__supply_movements__supply_id'), 'supply_movements', ['supply_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__supply_movements__batch_id'), 'supply_movements', ['batch_id'], unique=False, schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.supplies"}).scalar() is None:
        return

    op.drop_table('supply_movements', schema=schema)
    op.drop_table('recipe_items', schema=schema)
    op.drop_table('recipes', schema=schema)
    op.drop_table('supply_batches', schema=schema)
    op.drop_table('supplies', schema=schema)
