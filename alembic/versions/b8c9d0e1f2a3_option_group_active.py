"""option_groups.active (soft-delete de grupos de opciones)

Revision ID: b8c9d0e1f2a3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "option_groups"):
        return
    op.add_column("option_groups", sa.Column("active", sa.Boolean(), nullable=False,
                                             server_default="true"), schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "option_groups"):
        return
    op.drop_column("option_groups", "active", schema=schema)
