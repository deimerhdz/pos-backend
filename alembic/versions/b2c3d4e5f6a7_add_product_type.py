"""add product_type column to products with CHECK (INGREDIENT/PRODUCT/RECIPE)

Revision ID: b2c3d4e5f6a7
Revises: 452fd9a91a58
Create Date: 2026-06-07 16:40:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '452fd9a91a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.add_column(
        'products',
        sa.Column('product_type', sa.String(length=50), nullable=False, server_default='PRODUCT'),
        schema=schema,
    )
    op.create_check_constraint(
        op.f('ck__products__ck_product_type'),
        'products',
        "product_type IN ('INGREDIENT', 'PRODUCT', 'RECIPE')",
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f('ck__products__ck_product_type'), 'products', schema=schema, type_='check'
    )
    op.drop_column('products', 'product_type', schema=schema)
