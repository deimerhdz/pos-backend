"""product_component schema (componentes de recetas)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-07 17:30:00.000000

Crea la tabla puente `product_component` en cada esquema de tenant. Relaciona un
producto-receta (`product_id`) con los productos que actúan como sus ingredientes
(`component_id`) y la cantidad requerida de cada uno (`quantity`). Ambos FK
apuntan a `products` del mismo esquema; unique en (product_id, component_id).
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.create_table('product_component',
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('component_id', sa.UUID(), nullable=False),
    sa.Column('quantity', sa.Numeric(12, 3), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False, comment='Unique record identifier'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Record creation time'),
    sa.Column('updated_at', sa.DateTime(), nullable=True, comment='Unique record identifier'),
    sa.ForeignKeyConstraint(['product_id'], [f'{schema}.products.id'], name=op.f('fk__product_component__product_id__products')),
    sa.ForeignKeyConstraint(['component_id'], [f'{schema}.products.id'], name=op.f('fk__product_component__component_id__products')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk__product_component')),
    sa.UniqueConstraint('product_id', 'component_id', name=op.f('uq__product_component__product_id_component_id')),
    schema=schema
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    op.drop_table('product_component', schema=schema)
