"""siembra el plan transicional "Ilimitado (transición)" en shared.plans

Revision ID: 5eeb92818839
Revises: 236f34af96d3
Create Date: 2026-08-24 00:00:00.000001

"""
import uuid
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '5eeb92818839'
down_revision: Union[str, Sequence[str], None] = '236f34af96d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLAN_NAME = "Ilimitado (transición)"


def upgrade() -> None:
    """Siembra un único plan sin límites, con los tres módulos incluidos y
    sin precios ni ciclo de facturación — reproduce exactamente el
    comportamiento sin restricciones que todo tenant ya tenía antes de esta
    spec (research.md Decisión 3, Principio II). Los tenants existentes se
    backfillean a este plan en la migración siguiente
    ({rev}_tenant_plan_assignment.py). Idempotente por `name`."""
    bind = op.get_bind()
    exists = bind.execute(
        text("SELECT 1 FROM shared.plans WHERE name = :name"), {"name": _PLAN_NAME}
    ).scalar()
    if exists:
        return
    bind.execute(
        text(
            "INSERT INTO shared.plans "
            "(id, name, description, mesas_limit, cajas_limit, usuarios_limit, "
            "productos_limit, metodos_pago_activos_limit, inventario_access, "
            "compras_access, promociones_access, precio_mensual, precio_anual, created_at) "
            "VALUES (:id, :name, :description, NULL, NULL, NULL, NULL, NULL, "
            "true, true, true, NULL, NULL, now())"
        ),
        {
            "id": uuid.uuid4(),
            "name": _PLAN_NAME,
            "description": (
                "Plan sembrado por la migración de la spec 033 para que ningún tenant "
                "existente vea su comportamiento alterado el día del despliegue. Sin "
                "límites, con los tres módulos incluidos, sin precio ni vencimiento."
            ),
        },
    )


def downgrade() -> None:
    """Borra únicamente la fila sembrada por nombre (Principio VIII) —
    solo tiene sentido ejecutar este downgrade después de revertir
    {rev}_tenant_plan_assignment.py, que es quien la referencia."""
    bind = op.get_bind()
    bind.execute(text("DELETE FROM shared.plans WHERE name = :name"), {"name": _PLAN_NAME})
