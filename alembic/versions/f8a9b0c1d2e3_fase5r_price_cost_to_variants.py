"""refactor fase5: price/cost solo en variants (drop products.price/cost)

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-07-10 18:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'f8a9b0c1d2e3'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.{table}"}).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not (_has(schema, "variants") and _has(schema, "products")):
        return
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    sq = preparer.format_schema(schema)

    op.add_column('variants', sa.Column('cost', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    # Backfill: el costo de la variante hereda el del producto (price ya estaba en la variante).
    op.execute(sa.text(f"UPDATE {sq}.variants v SET cost = p.cost FROM {sq}.products p WHERE v.product_id = p.id"))

    op.drop_column('products', 'price', schema=schema)
    op.drop_column('products', 'cost', schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has(schema, "products"):
        return
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    sq = preparer.format_schema(schema)

    op.add_column('products', sa.Column('price', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    op.add_column('products', sa.Column('cost', sa.Numeric(10, 2), server_default=sa.text('0'), nullable=False), schema=schema)
    op.execute(sa.text(
        f"UPDATE {sq}.products p SET price = v.price, cost = v.cost "
        f"FROM {sq}.variants v WHERE v.product_id = p.id AND v.is_default = true"
    ))
    op.drop_column('variants', 'cost', schema=schema)
