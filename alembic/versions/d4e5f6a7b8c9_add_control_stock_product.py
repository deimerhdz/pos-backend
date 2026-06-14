"""add control_stock column to products

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-07 18:00:00.000000

Re-agrega la columna `control_stock` (boolean, default false) a `products` en cada
esquema de tenant. Determina si un producto inicializa/gestiona inventario.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column('control_stock', sa.Boolean(), nullable=False, server_default='false'),
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    op.drop_column('products', 'control_stock', schema=schema)
