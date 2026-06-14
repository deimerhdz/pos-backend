from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = [
    {'name': 'SUPER_ADMIN'},
    {'name': 'ADMIN'},
    {'name': 'CASHIER'},
]


def upgrade() -> None:
    now = datetime.now(timezone.utc)

    roles_table = sa.table(
        "roles",
        sa.column("id", sa.UUID),
        sa.column("name", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        schema="shared"
    )

    op.bulk_insert(
        roles_table,
        [
            {
                "id": uuid.uuid4(),
                "name": r["name"],
                "active": True,
                "created_at": now
            }
            for r in ROLES
        ]
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM shared.roles WHERE name IN ('SUPER_ADMIN', 'ADMIN', 'CASHIER')")
    )
