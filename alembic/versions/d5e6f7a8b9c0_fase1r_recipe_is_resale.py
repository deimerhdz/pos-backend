"""refactor fase1: recipes.is_resale (receta obligatoria / reventa 1:1)

Revision ID: d5e6f7a8b9c0
Revises: c3d4f4e5a6b7
Create Date: 2026-07-10 16:00:00.000000

"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c3d4f4e5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(schema: str, table: str) -> bool:
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": f"{schema}.{table}"}).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has(schema, "recipes"):
        return
    op.add_column(
        'recipes',
        sa.Column('is_resale', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has(schema, "recipes"):
        return
    op.drop_column('recipes', 'is_resale', schema=schema)
