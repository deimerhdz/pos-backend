"""habilita tipo de orden Domicilio: campos de entrega + valor del domicilio

Spec 056: agrega `delivery_address`, `delivery_phone` y `delivery_fee` a
`customer_orders` (solo diligenciados cuando `order_type == 'DELIVERY'`, sin
ningún valor por defecto — spec.md FR-006), y `delivery_fee` a `sales` (copia
del valor de la orden al facturar, research.md Decisión 5). Columnas
puramente aditivas y nulables — sin backfill: ningún pedido histórico es de
tipo DELIVERY (ese valor no tenía ningún punto de creación real hasta esta
spec, research.md Decisión 10).

Revision ID: d427cd419e79
Revises: 03c1cc5bfeb2
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'd427cd419e79'
down_revision: Union[str, Sequence[str], None] = '03c1cc5bfeb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CK_DELIVERY_FEE = "ck_customer_order_delivery_fee_non_negative"


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if _has_table(schema, "customer_orders"):
        op.add_column(
            "customer_orders", sa.Column("delivery_address", sa.String(255), nullable=True),
            schema=schema,
        )
        op.add_column(
            "customer_orders", sa.Column("delivery_phone", sa.String(30), nullable=True),
            schema=schema,
        )
        op.add_column(
            "customer_orders", sa.Column("delivery_fee", sa.Numeric(12, 2), nullable=True),
            schema=schema,
        )
        op.create_check_constraint(
            _CK_DELIVERY_FEE, "customer_orders",
            "delivery_fee IS NULL OR delivery_fee >= 0",
            schema=schema,
        )

    if _has_table(schema, "sales"):
        op.add_column(
            "sales", sa.Column("delivery_fee", sa.Numeric(12, 2), nullable=True), schema=schema,
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if _has_table(schema, "sales"):
        op.drop_column("sales", "delivery_fee", schema=schema)

    if _has_table(schema, "customer_orders"):
        op.drop_constraint(_CK_DELIVERY_FEE, "customer_orders", type_="check", schema=schema)
        op.drop_column("customer_orders", "delivery_fee", schema=schema)
        op.drop_column("customer_orders", "delivery_phone", schema=schema)
        op.drop_column("customer_orders", "delivery_address", schema=schema)
