"""add must_change_password column to users

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-12 00:00:00.000000

Agrega la columna `must_change_password` (boolean, default false) a `shared.users`.
La activa el flujo superadmin-crea-tenant para forzar el cambio de contraseña en el
primer inicio de sesión.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        schema='shared',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'must_change_password', schema='shared')
