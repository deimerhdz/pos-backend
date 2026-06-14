from typing import Sequence, Union
import uuid
from datetime import datetime, timezone

import bcrypt

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPER_ADMIN_EMAIL = "admin@admin.com"
SUPER_ADMIN_NAME = "Super Admin"
SUPER_ADMIN_PASSWORD = "Admin1234!"


def upgrade() -> None:
    bind = op.get_bind()

    role = bind.execute(
        sa.text("SELECT id FROM shared.roles WHERE name = 'SUPER_ADMIN' LIMIT 1")
    ).fetchone()

    if role is None:
        raise RuntimeError("SUPER_ADMIN role not found. Run migration 0001 first.")

    users_table = sa.table(
        "users",
        sa.column("id", sa.UUID),
        sa.column("name", sa.String),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("active", sa.Boolean),
        sa.column("role_id", sa.UUID),
        sa.column("tenant_id", sa.Integer),
        sa.column("created_at", sa.DateTime),
        schema="shared",
    )

    op.bulk_insert(
        users_table,
        [{
            "id": uuid.uuid4(),
            "name": SUPER_ADMIN_NAME,
            "email": SUPER_ADMIN_EMAIL,
            "password_hash": bcrypt.hashpw(SUPER_ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode(),
            "active": True,
            "role_id": role[0],
            "tenant_id": None,
            "created_at": datetime.now(timezone.utc),
        }],
    )


def downgrade() -> None:
    op.execute(
        sa.text(f"DELETE FROM shared.users WHERE email = '{SUPER_ADMIN_EMAIL}'")
    )
