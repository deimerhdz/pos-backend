"""reabrir sesiones de mesa cerradas que aún tienen pedidos sin cobrar

El barrido de sesiones huérfanas cerraba por antigüedad (`TABLE_SESSION_MAX_HOURS`)
sin mirar si quedaba algo que cobrar. La mesa quedaba `ocupada` con su pedido vivo,
pero `GET /table-sessions` solo lista las `active`, así que la terminal no podía
cargar la cuenta: imposible de cobrar y de liberar.

Esta migración repara esas mesas devolviendo la sesión a `active`. El barrido ya no
las cierra (ahora solo echa a los comensales), así que no vuelven a aparecer.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from app.scripts.tenant import for_each_tenant_schema

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Reabre solo lo que está descuadrado; es idempotente."""
    if not _has_table(schema, "table_sessions"):
        return

    op.execute(text(f"""
        UPDATE "{schema}".table_sessions ts
           SET status = 'active', closed_at = NULL
         WHERE ts.status = 'closed'
           AND EXISTS (
               SELECT 1 FROM "{schema}".customer_orders o
                WHERE o.table_session_id = ts.id
                  AND o.status NOT IN ('pagada', 'cancelada')
           )
    """))


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Sin vuelta atrás: volver a cerrarlas reintroduciría el descuadre, y no se
    guarda cuáles se reabrieron aquí."""
    pass
