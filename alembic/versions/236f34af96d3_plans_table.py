"""planes de suscripción administrados por el Super Admin: shared.plans

Revision ID: 236f34af96d3
Revises: 04b3d1d3e15f
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '236f34af96d3'
down_revision: Union[str, Sequence[str], None] = '04b3d1d3e15f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


def upgrade() -> None:
    """Upgrade schema. shared.plans es una tabla única (no per-tenant) — sin
    seed de datos: el plan transicional se siembra en su propia migración
    de datos (spec 033, {rev}_seed_transitional_plan.py, Principio VI: no
    mezclar creación de esquema con migración de datos)."""
    if _has_table("shared", "plans"):
        return

    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        # Sin server_default: "0 si no se configura" (FR-002) se resuelve en
        # la capa Pydantic (PlanCreate), no en la base de datos — un
        # server_default aquí haría que el ORM omita del INSERT cualquier
        # asignación explícita de NULL (el sentinel "ilimitado", FR-007),
        # reemplazándola silenciosamente por el default. Ver app/models/plan.py.
        sa.Column("mesas_limit", sa.Integer(), nullable=True),
        sa.Column("cajas_limit", sa.Integer(), nullable=True),
        sa.Column("usuarios_limit", sa.Integer(), nullable=True),
        sa.Column("productos_limit", sa.Integer(), nullable=True),
        sa.Column("metodos_pago_activos_limit", sa.Integer(), nullable=True),
        sa.Column("inventario_access", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("compras_access", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("promociones_access", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("precio_mensual", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("precio_anual", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__plans")),
        sa.UniqueConstraint("name", name=op.f("uq__plans__name")),
        schema="shared",
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table("shared", "plans"):
        return
    op.drop_table("plans", schema="shared")
