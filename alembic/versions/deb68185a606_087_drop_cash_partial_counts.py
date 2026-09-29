"""087 drop cash partial counts

Revision ID: deb68185a606
Revises: b605b7c334f2
Create Date: 2026-09-28 17:12:43.275744

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'deb68185a606'
down_revision: Union[str, Sequence[str], None] = 'b605b7c334f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """spec 087, FR-001 (A-87): elimina Arqueo Parcial por completo -- borrado
    físico permanente del historial, decisión de negocio intencional. NO
    ejecutar en un entorno con datos reales sin confirmar backup antes."""
    if not _has_table(schema, "cash_partial_counts"):
        return
    # Sin `op.drop_index()` explícito a propósito: `DROP TABLE` ya se lleva
    # todos sus índices consigo, y el nombre real del índice en bases ya
    # existentes puede no coincidir con el que generaría `op.f()` hoy (visto
    # en la práctica: `ix_tenant_cash_partial_counts_cash_shift_id` en vez de
    # `ix__cash_partial_counts__cash_shift_id`, deriva de la convención de
    # nombres vigente cuando se creó la tabla original en f5a6b7c8d9e0).
    op.drop_table("cash_partial_counts", schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Recrea la tabla vacía (estructura idéntica a f5a6b7c8d9e0) -- los datos
    borrados en upgrade() NO se recuperan, el borrado es intencionalmente
    permanente (FR-001, decisión de negocio)."""
    if _has_table(schema, "cash_partial_counts"):
        return
    # `cash_partial_counts` depende de `cash_shifts` vía FK -- sin ella, este
    # no es un schema de tenant real y completo (visto en la práctica: un
    # `tenant_default` de plantilla, sin `cash_shifts`), así que no hay nada
    # que recrear.
    if not _has_table(schema, "cash_shifts"):
        return
    op.create_table(
        "cash_partial_counts",
        sa.Column("cash_shift_id", sa.UUID(), nullable=False),
        sa.Column("counted_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("difference", sa.Numeric(12, 2), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("user_name", sa.String(length=255), nullable=True),
        sa.Column("counted_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cash_shift_id"], [f"{schema}.cash_shifts.id"],
            name=op.f("fk__cash_partial_counts__cash_shift_id__cash_shifts"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__cash_partial_counts")),
        schema=schema,
    )
    op.create_index(op.f("ix__cash_partial_counts__cash_shift_id"), "cash_partial_counts",
                     ["cash_shift_id"], schema=schema)
