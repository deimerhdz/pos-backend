"""estandariza canal y agrega tipo de orden: customer_orders

Spec 055: reemplaza los valores libres del canal (`qr`/`counter`/`waiter`) por
un catálogo fijo estandarizado (`POS`/`QR_MENU`/`WHATSAPP`/`API`), agrega
`order_type` (`DINE_IN`/`TAKEAWAY`/`DELIVERY`, nulable) y la columna técnica
`is_consolidation_order` (no expuesta en la API — research.md D2: preserva la
distinción interna que antes vivía en `channel == 'waiter'`, usada por
`orders.consolidation.get_or_create_open_order` para no reabrir por accidente
una comanda de mostrador ya cobrada).

Revision ID: 03c1cc5bfeb2
Revises: c8ff3a5551cb
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '03c1cc5bfeb2'
down_revision: Union[str, Sequence[str], None] = 'c8ff3a5551cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Nombres cortos: la naming convention de `target_metadata` los expande a
# `ck__customer_orders__<nombre>`, que es como quedan en la BD.
_CK_CHANNEL = "ck_customer_order_channel"
_CK_ORDER_TYPE = "ck_customer_order_order_type"


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "customer_orders"):
        return

    op.add_column(
        "customer_orders", sa.Column("order_type", sa.String(10), nullable=True), schema=schema,
    )
    op.add_column(
        "customer_orders",
        sa.Column(
            "is_consolidation_order", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        schema=schema,
    )

    # Backfill (spec.md, Clarifications sesión 2026-08-29; data-model.md):
    # DINE_IN para los que ya tienen mesa asignada; el resto queda sin tipo de
    # orden asignado (NULL). `is_consolidation_order` reconstruye qué filas
    # entraban hoy por `orders.consolidation.get_or_create_open_order` (única
    # función que creaba/reusaba órdenes con channel='waiter').
    op.execute(text(
        f'UPDATE "{schema}".customer_orders SET order_type = \'DINE_IN\' '
        f'WHERE dining_table_id IS NOT NULL'
    ))
    op.execute(text(
        f'UPDATE "{schema}".customer_orders SET is_consolidation_order = true '
        f"WHERE channel = 'waiter'"
    ))

    # El constraint viejo (channel IN ('qr','counter','waiter')) sigue activo
    # hasta aquí — hay que quitarlo antes de escribir los valores nuevos, o el
    # UPDATE de abajo viola esa restricción a mitad de camino.
    op.drop_constraint(_CK_CHANNEL, "customer_orders", type_="check", schema=schema)

    # Remapeo del canal a los 4 valores estandarizados (research.md D2):
    # 'qr' -> QR_MENU; 'counter' y 'waiter' -> POS (ambos son personal del
    # punto de venta; la distinción técnica entre ellos ya quedó preservada
    # arriba en is_consolidation_order).
    op.execute(text(
        f'UPDATE "{schema}".customer_orders SET channel = CASE channel '
        f"WHEN 'qr' THEN 'QR_MENU' "
        f"WHEN 'counter' THEN 'POS' "
        f"WHEN 'waiter' THEN 'POS' "
        f"END"
    ))

    op.create_check_constraint(
        _CK_CHANNEL, "customer_orders",
        "channel IN ('POS', 'QR_MENU', 'WHATSAPP', 'API')",
        schema=schema,
    )
    op.create_check_constraint(
        _CK_ORDER_TYPE, "customer_orders",
        "order_type IS NULL OR order_type IN ('DINE_IN', 'TAKEAWAY', 'DELIVERY')",
        schema=schema,
    )

    op.create_index("idx_customer_orders_channel", "customer_orders", ["channel"], schema=schema)
    op.create_index(
        "idx_customer_orders_order_type", "customer_orders", ["order_type"], schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "customer_orders"):
        return

    op.drop_index("idx_customer_orders_order_type", "customer_orders", schema=schema)
    op.drop_index("idx_customer_orders_channel", "customer_orders", schema=schema)

    op.drop_constraint(_CK_ORDER_TYPE, "customer_orders", type_="check", schema=schema)
    # El constraint nuevo sigue activo hasta aquí — hay que quitarlo antes de
    # escribir los valores viejos, o el UPDATE de abajo lo viola a mitad de
    # camino (mismo motivo que en upgrade()).
    op.drop_constraint(_CK_CHANNEL, "customer_orders", type_="check", schema=schema)

    # Revierte el canal: QR_MENU -> qr; POS -> counter o waiter, según
    # is_consolidation_order (la única señal que distinguía ambos antes de
    # esta migración).
    op.execute(text(
        f'UPDATE "{schema}".customer_orders SET channel = CASE '
        f"WHEN channel = 'QR_MENU' THEN 'qr' "
        f"WHEN channel = 'POS' AND is_consolidation_order THEN 'waiter' "
        f"WHEN channel = 'POS' THEN 'counter' "
        f"END"
    ))

    op.create_check_constraint(
        _CK_CHANNEL, "customer_orders",
        "channel IN ('qr', 'counter', 'waiter')",
        schema=schema,
    )

    op.drop_column("customer_orders", "is_consolidation_order", schema=schema)
    op.drop_column("customer_orders", "order_type", schema=schema)
