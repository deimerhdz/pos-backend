"""Simplifica estado_cocina: se elimina 'entregado'

Al deprecar el KDS, el ciclo del ítem se resuelve desde la terminal de mesas y
'entregado' deja de aportar una decisión distinta de 'listo': en ambos casos el
insumo ya se consumió y el ítem se puede cobrar. Las filas existentes se
consolidan en 'listo'.

Revision ID: c5d6e7f8a9b0
Revises: 01a0d2359c2f
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
from sqlalchemy import text

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, Sequence[str], None] = '01a0d2359c2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# El nombre corto: la naming convention de `target_metadata` lo expande a
# `ck__order_items__ck_order_item_estado_cocina`, que es como está en la BD.
_CK = "ck_order_item_estado_cocina"


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "order_items"):
        return
    op.execute(text(
        f"UPDATE \"{schema}\".order_items SET estado_cocina = 'listo' "
        f"WHERE estado_cocina = 'entregado'"
    ))
    op.drop_constraint(_CK, "order_items", type_="check", schema=schema)
    op.create_check_constraint(
        _CK, "order_items",
        "estado_cocina IN ('pendiente', 'en_preparacion', 'listo', 'anulado')",
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "order_items"):
        return
    # No hay datos que revertir: 'listo' es válido en ambos dominios y no se
    # puede saber qué filas fueron 'entregado' antes de la consolidación.
    op.drop_constraint(_CK, "order_items", type_="check", schema=schema)
    op.create_check_constraint(
        _CK, "order_items",
        "estado_cocina IN ('pendiente', 'en_preparacion', 'listo', 'entregado', 'anulado')",
        schema=schema,
    )
