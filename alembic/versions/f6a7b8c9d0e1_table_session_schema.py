"""table_session schema (sesiones de comensales por mesa)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-13 00:00:00.000000

Crea la tabla `table_sessions` en cada esquema de tenant. Registra las sesiones de
comensales que escanean el QR de una mesa (nombre + token). El número de sesiones
activas por mesa lo limita `tables.capacity`; las cierra el staff al liberar la mesa.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.create_table(
        'table_sessions',
        sa.Column('table_id', sa.UUID(), nullable=False),
        sa.Column('customer_name', sa.String(length=255), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('id', sa.UUID(), nullable=False, comment='Unique record identifier'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Record creation time'),
        sa.Column('updated_at', sa.DateTime(), nullable=True, comment='Unique record identifier'),
        sa.ForeignKeyConstraint(['table_id'], [f'{schema}.tables.id'], name=op.f('fk__table_sessions__table_id__tables')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk__table_sessions')),
        schema=schema,
    )
    op.create_index(
        op.f('ix__table_sessions__token'), 'table_sessions', ['token'], unique=True, schema=schema
    )
    op.create_index(
        op.f('ix__table_sessions__table_id'), 'table_sessions', ['table_id'], schema=schema
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix__table_sessions__table_id'), table_name='table_sessions', schema=schema)
    op.drop_index(op.f('ix__table_sessions__token'), table_name='table_sessions', schema=schema)
    op.drop_table('table_sessions', schema=schema)
