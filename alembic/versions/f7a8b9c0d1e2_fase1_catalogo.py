"""fase1 catalogo (atributos, variantes, modificadores, impuestos)

Revision ID: f7a8b9c0d1e2
Revises: a7b8c9d0e1f2
Create Date: 2026-07-10 12:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _std_cols():
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    ]


def _has_base_tables(schema: str) -> bool:
    """La Fase 1 extiende products/unit_measures; solo aplica en schemas que ya
    los tienen. El schema plantilla 'tenant_default' puede no tenerlos si la BD se
    inicializó por create_all + stamp en vez de correr las migraciones base."""
    bind = op.get_bind()
    return bind.execute(
        sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.products"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_base_tables(schema):
        return

    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    sq = preparer.format_schema(schema)

    # --- attributes ---
    op.create_table(
        'attributes',
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('affects_inventory', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__attributes')),
        schema=schema,
    )
    op.create_index(op.f('ix__attributes__name'), 'attributes', ['name'], unique=True, schema=schema)

    # --- attribute_values ---
    op.create_table(
        'attribute_values',
        sa.Column('attribute_id', sa.UUID(), nullable=False),
        sa.Column('value', sa.String(150), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['attribute_id'], [f'{schema}.attributes.id'], name=op.f('fk__attribute_values__attribute_id__attributes')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__attribute_values')),
        sa.UniqueConstraint('attribute_id', 'value', name=op.f('uq__attribute_values__attribute_id_value')),
        schema=schema,
    )
    op.create_index(op.f('ix__attribute_values__attribute_id'), 'attribute_values', ['attribute_id'], unique=False, schema=schema)

    # --- product_attributes ---
    op.create_table(
        'product_attributes',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('attribute_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__product_attributes__product_id__products')),
        sa.ForeignKeyConstraint(['attribute_id'], [f'{schema}.attributes.id'], name=op.f('fk__product_attributes__attribute_id__attributes')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__product_attributes')),
        sa.UniqueConstraint('product_id', 'attribute_id', name=op.f('uq__product_attributes__product_id_attribute_id')),
        schema=schema,
    )
    op.create_index(op.f('ix__product_attributes__product_id'), 'product_attributes', ['product_id'], unique=False, schema=schema)

    # --- variants ---
    op.create_table(
        'variants',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('sku', sa.String(100), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__variants__product_id__products')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__variants')),
        schema=schema,
    )
    op.create_index(op.f('ix__variants__sku'), 'variants', ['sku'], unique=True, schema=schema)
    op.create_index(op.f('ix__variants__product_id'), 'variants', ['product_id'], unique=False, schema=schema)

    # --- variant_values ---
    op.create_table(
        'variant_values',
        sa.Column('variant_id', sa.UUID(), nullable=False),
        sa.Column('attribute_value_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['variant_id'], [f'{schema}.variants.id'], name=op.f('fk__variant_values__variant_id__variants')),
        sa.ForeignKeyConstraint(['attribute_value_id'], [f'{schema}.attribute_values.id'], name=op.f('fk__variant_values__attribute_value_id__attribute_values')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__variant_values')),
        sa.UniqueConstraint('variant_id', 'attribute_value_id', name=op.f('uq__variant_values__variant_id_attribute_value_id')),
        schema=schema,
    )
    op.create_index(op.f('ix__variant_values__variant_id'), 'variant_values', ['variant_id'], unique=False, schema=schema)

    # --- modifier_groups ---
    op.create_table(
        'modifier_groups',
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('required', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('min_select', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('max_select', sa.Integer(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__modifier_groups')),
        schema=schema,
    )

    # --- modifiers ---
    op.create_table(
        'modifiers',
        sa.Column('group_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['group_id'], [f'{schema}.modifier_groups.id'], name=op.f('fk__modifiers__group_id__modifier_groups')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__modifiers')),
        schema=schema,
    )
    op.create_index(op.f('ix__modifiers__group_id'), 'modifiers', ['group_id'], unique=False, schema=schema)

    # --- product_modifier_groups ---
    op.create_table(
        'product_modifier_groups',
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('group_id', sa.UUID(), nullable=False),
        *_std_cols(),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__product_modifier_groups__product_id__products')),
        sa.ForeignKeyConstraint(['group_id'], [f'{schema}.modifier_groups.id'], name=op.f('fk__product_modifier_groups__group_id__modifier_groups')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__product_modifier_groups')),
        sa.UniqueConstraint('product_id', 'group_id', name=op.f('uq__product_modifier_groups__product_id_group_id')),
        schema=schema,
    )
    op.create_index(op.f('ix__product_modifier_groups__product_id'), 'product_modifier_groups', ['product_id'], unique=False, schema=schema)

    # --- taxes ---
    op.create_table(
        'taxes',
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('rate', sa.Numeric(5, 2), nullable=False),
        sa.Column('inclusive', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_std_cols(),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__taxes')),
        schema=schema,
    )

    # --- tax_links ---
    op.create_table(
        'tax_links',
        sa.Column('tax_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=True),
        sa.Column('variant_id', sa.UUID(), nullable=True),
        *_std_cols(),
        sa.ForeignKeyConstraint(['tax_id'], [f'{schema}.taxes.id'], name=op.f('fk__tax_links__tax_id__taxes')),
        sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__tax_links__product_id__products')),
        sa.ForeignKeyConstraint(['variant_id'], [f'{schema}.variants.id'], name=op.f('fk__tax_links__variant_id__variants')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__tax_links')),
        sa.CheckConstraint('(product_id IS NOT NULL)::int + (variant_id IS NOT NULL)::int = 1', name='ck_tax_link_single_target'),
        schema=schema,
    )
    op.create_index(op.f('ix__tax_links__tax_id'), 'tax_links', ['tax_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__tax_links__product_id'), 'tax_links', ['product_id'], unique=False, schema=schema)
    op.create_index(op.f('ix__tax_links__variant_id'), 'tax_links', ['variant_id'], unique=False, schema=schema)

    # --- extender unit_measures ---
    op.add_column('unit_measures', sa.Column('dimension', sa.String(20), server_default=sa.text("'COUNT'"), nullable=False), schema=schema)
    op.add_column('unit_measures', sa.Column('factor_to_base', sa.Numeric(12, 4), server_default=sa.text('1'), nullable=False), schema=schema)
    op.execute(sa.text(f"UPDATE {sq}.unit_measures SET dimension='MASS', factor_to_base=1 WHERE lower(abbreviation) IN ('g','gr','gramo')"))
    op.execute(sa.text(f"UPDATE {sq}.unit_measures SET dimension='MASS', factor_to_base=1000 WHERE lower(abbreviation) IN ('kg','kilogramo')"))
    op.execute(sa.text(f"UPDATE {sq}.unit_measures SET dimension='VOLUME', factor_to_base=1 WHERE lower(abbreviation) IN ('ml','mililitro')"))
    op.execute(sa.text(f"UPDATE {sq}.unit_measures SET dimension='VOLUME', factor_to_base=1000 WHERE lower(abbreviation) IN ('l','lt','litro')"))
    op.create_check_constraint('ck_unit_measure_dimension', 'unit_measures', "dimension IN ('MASS', 'VOLUME', 'COUNT')", schema=schema)

    # --- extender products ---
    op.add_column('products', sa.Column('type', sa.String(50), server_default=sa.text("'SIMPLE'"), nullable=False), schema=schema)
    op.add_column('products', sa.Column('image_url', sa.String(500), nullable=True), schema=schema)
    op.create_check_constraint('ck_product_kind', 'products', "type IN ('SIMPLE', 'CONFIGURABLE')", schema=schema)
    op.create_unique_constraint('uq__products__category_id__name', 'products', ['category_id', 'name'], schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.attributes"}).scalar() is None:
        return

    op.drop_constraint('uq__products__category_id__name', 'products', schema=schema, type_='unique')
    op.drop_constraint('ck_product_kind', 'products', schema=schema, type_='check')
    op.drop_column('products', 'image_url', schema=schema)
    op.drop_column('products', 'type', schema=schema)

    op.drop_constraint('ck_unit_measure_dimension', 'unit_measures', schema=schema, type_='check')
    op.drop_column('unit_measures', 'factor_to_base', schema=schema)
    op.drop_column('unit_measures', 'dimension', schema=schema)

    op.drop_table('tax_links', schema=schema)
    op.drop_table('taxes', schema=schema)
    op.drop_table('product_modifier_groups', schema=schema)
    op.drop_table('modifiers', schema=schema)
    op.drop_table('modifier_groups', schema=schema)
    op.drop_table('variant_values', schema=schema)
    op.drop_table('variants', schema=schema)
    op.drop_table('product_attributes', schema=schema)
    op.drop_table('attribute_values', schema=schema)
    op.drop_table('attributes', schema=schema)
