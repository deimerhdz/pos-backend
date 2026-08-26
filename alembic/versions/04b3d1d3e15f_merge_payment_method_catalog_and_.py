"""merge payment_method_catalog and password_reset_tokens branches

Revision ID: 04b3d1d3e15f
Revises: 130642d23e76, d252a23e65a1
Create Date: 2026-08-24 18:36:44.939547

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04b3d1d3e15f'
down_revision: Union[str, Sequence[str], None] = ('130642d23e76', 'd252a23e65a1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)
    
    pass


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)
    
    pass
