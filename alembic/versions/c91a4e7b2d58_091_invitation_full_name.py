"""091 nombre completo en invitaciones (user_invitations.name)

Revision ID: c91a4e7b2d58
Revises: a89f0c1d2e3b
Create Date: 2026-10-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c91a4e7b2d58'
down_revision: Union[str, Sequence[str], None] = 'a89f0c1d2e3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Aditivo, sin default ni backfill: las invitaciones anteriores quedan con
    # `name` nulo y se siguen tratando como hoy (spec 091, FR-019/FR-024).
    op.add_column(
        'user_invitations',
        sa.Column('name', sa.String(length=100), nullable=True),
        schema='shared',
    )


def downgrade() -> None:
    op.drop_column('user_invitations', 'name', schema='shared')
