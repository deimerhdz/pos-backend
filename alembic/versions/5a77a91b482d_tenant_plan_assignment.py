"""Tenant.plan_id/ciclo_facturacion/plan_iniciado_en/plan_vence_en, baja de plan (heredada)

Revision ID: 5a77a91b482d
Revises: 5eeb92818839
Create Date: 2026-08-24 00:00:00.000002

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '5a77a91b482d'
down_revision: Union[str, Sequence[str], None] = '5eeb92818839'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRANSITIONAL_PLAN_NAME = "Ilimitado (transición)"


def _has_column(schema: str, table: str, column: str) -> bool:
    return op.get_bind().execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": schema, "t": table, "c": column},
    ).scalar() is not None


def upgrade() -> None:
    """Upgrade schema. shared.tenants es una tabla única (no per-tenant).

    Agrega plan_id (nullable), ciclo_facturacion/plan_iniciado_en/plan_vence_en
    (nullable para siempre, no solo durante la migración — research.md
    Decisión 10/12), backfillea plan_id de TODAS las filas existentes al plan
    transicional (dejando ciclo/fechas en NULL, es decir "sin vencimiento",
    Principio II), fuerza plan_id a NOT NULL, y elimina la columna heredada
    `plan` (String, sin uso real — research.md Decisión 2).

    Las cuatro operaciones van en una sola migración porque el backfill de
    plan_id es 100% determinístico (una sola fila destino para todos los
    tenants existentes) — a diferencia del backfill difuso de spec 032,
    research.md Decisión 3."""
    bind = op.get_bind()

    if not _has_column("shared", "tenants", "plan_id"):
        op.add_column(
            "tenants",
            sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="shared",
        )
        op.create_foreign_key(
            op.f("fk__tenants__plan_id__plans"),
            "tenants",
            "plans",
            ["plan_id"],
            ["id"],
            source_schema="shared",
            referent_schema="shared",
        )

    if not _has_column("shared", "tenants", "ciclo_facturacion"):
        op.add_column(
            "tenants",
            sa.Column("ciclo_facturacion", sa.String(length=10), nullable=True),
            schema="shared",
        )
        op.create_check_constraint(
            op.f("ck__tenants__ck_tenants_ciclo_facturacion"),
            "tenants",
            "ciclo_facturacion IN ('mensual', 'anual')",
            schema="shared",
        )

    if not _has_column("shared", "tenants", "plan_iniciado_en"):
        op.add_column(
            "tenants",
            sa.Column("plan_iniciado_en", sa.DateTime(), nullable=True),
            schema="shared",
        )

    if not _has_column("shared", "tenants", "plan_vence_en"):
        op.add_column(
            "tenants",
            sa.Column("plan_vence_en", sa.DateTime(), nullable=True),
            schema="shared",
        )

    plan_id = bind.execute(
        text("SELECT id FROM shared.plans WHERE name = :name"),
        {"name": _TRANSITIONAL_PLAN_NAME},
    ).scalar()
    if plan_id is None:
        raise RuntimeError(
            f"No se encontró el plan transicional '{_TRANSITIONAL_PLAN_NAME}'. "
            "Ejecute primero la migración 5eeb92818839_seed_transitional_plan."
        )
    bind.execute(
        text("UPDATE shared.tenants SET plan_id = :plan_id WHERE plan_id IS NULL"),
        {"plan_id": plan_id},
    )

    op.alter_column("tenants", "plan_id", nullable=False, schema="shared")

    if _has_column("shared", "tenants", "plan"):
        op.drop_column("tenants", "plan", schema="shared")


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_column("shared", "tenants", "plan"):
        op.add_column(
            "tenants",
            sa.Column("plan", sa.String(length=100), nullable=False, server_default="basic"),
            schema="shared",
        )

    if _has_column("shared", "tenants", "plan_vence_en"):
        op.drop_column("tenants", "plan_vence_en", schema="shared")

    if _has_column("shared", "tenants", "plan_iniciado_en"):
        op.drop_column("tenants", "plan_iniciado_en", schema="shared")

    if _has_column("shared", "tenants", "ciclo_facturacion"):
        op.drop_constraint(
            op.f("ck__tenants__ck_tenants_ciclo_facturacion"), "tenants", schema="shared", type_="check"
        )
        op.drop_column("tenants", "ciclo_facturacion", schema="shared")

    if _has_column("shared", "tenants", "plan_id"):
        op.drop_constraint(
            op.f("fk__tenants__plan_id__plans"), "tenants", schema="shared", type_="foreignkey"
        )
        op.drop_column("tenants", "plan_id", schema="shared")
