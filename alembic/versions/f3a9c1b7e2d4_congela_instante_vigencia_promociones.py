"""congela el instante de vigencia de promociones al tomar el pedido

Spec 073 (FR-008, FR-011a): agrega `promotion_evaluated_at` a `customer_orders`
(instante congelado del pedido) y a `sales` (instante efectivamente usado al
facturar). Columnas puramente aditivas y nulables -- sin backfill: los pedidos
y ventas existentes conservan su comportamiento actual (evaluar la vigencia
con la hora del cobro), research.md D1/D12.

Revision ID: f3a9c1b7e2d4
Revises: 94144eaa60b5
Create Date: 2026-09-02 17:10:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'f3a9c1b7e2d4'
down_revision: Union[str, Sequence[str], None] = '94144eaa60b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # `DateTime(timezone=True)`: el instante se pasa a `local_now()`, que trata un
    # naive como hora local del tenant -- un UTC naive desplazaria la franja por el
    # offset. Se desvia a proposito del esqueleto de `d427cd419e79` en este punto
    # (research.md D1/D12, data-model.md).
    if _has_table(schema, "customer_orders"):
        op.add_column(
            "customer_orders",
            sa.Column("promotion_evaluated_at", sa.DateTime(timezone=True), nullable=True),
            schema=schema,
        )
    if _has_table(schema, "sales"):
        op.add_column(
            "sales",
            sa.Column("promotion_evaluated_at", sa.DateTime(timezone=True), nullable=True),
            schema=schema,
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if _has_table(schema, "sales"):
        op.drop_column("sales", "promotion_evaluated_at", schema=schema)
    if _has_table(schema, "customer_orders"):
        op.drop_column("customer_orders", "promotion_evaluated_at", schema=schema)
