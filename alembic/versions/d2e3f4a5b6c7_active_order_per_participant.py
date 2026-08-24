"""revisión y pago antes de enviar: idx_active_order_per_participant

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
from sqlalchemy import text

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Ancla: si no existe 'products' el schema no está inicializado (scratch).
    if not _has_table(schema, "products"):
        return

    # A lo sumo una orden activa por comensal (spec 025, FR-013,
    # data-model.md/research.md Decisión 4). Postgres no considera dos NULL
    # iguales: las órdenes de mostrador/mesero (participant_id NULL) no se
    # ven afectadas por este índice.
    op.create_index(
        "idx_active_order_per_participant",
        "customer_orders", ["participant_id"],
        unique=True, schema=schema,
        postgresql_where=text("status NOT IN ('pagada', 'cancelada')"),
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_index("idx_active_order_per_participant", "customer_orders", schema=schema)
