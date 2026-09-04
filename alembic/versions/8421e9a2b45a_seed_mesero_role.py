"""siembra el rol MESERO en shared.roles (spec 075)

Revision ID: 8421e9a2b45a
Revises: f3a9c1b7e2d4
Create Date: 2026-09-04 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '8421e9a2b45a'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1b7e2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLE_NAME = "MESERO"


def upgrade() -> None:
    """Agrega el rol MESERO al catálogo `shared.roles` (spec 075, data-model.md
    §Rol). `_seed_shared_data()` (app/core/db.py) solo siembra roles en la
    inicialización de una base de datos nueva — en cualquier ambiente que ya
    pasó por esa inicialización (todos los tenants reales hoy), agregar el
    valor solo a `ROLE_NAMES` no tiene ningún efecto, así que hace falta esta
    migración de datos. Idempotente por `name`, mismo criterio que
    `_seed_shared_data()` y que la siembra del catálogo de métodos de pago."""
    bind = op.get_bind()
    exists = bind.execute(
        text("SELECT 1 FROM shared.roles WHERE name = :name"),
        {"name": _ROLE_NAME},
    ).scalar()
    if exists:
        return
    bind.execute(
        text(
            "INSERT INTO shared.roles (id, name, active, created_at) "
            "VALUES (:id, :name, true, now())"
        ),
        {"id": uuid.uuid4(), "name": _ROLE_NAME},
    )


def downgrade() -> None:
    """Elimina la fila MESERO únicamente si ningún usuario o invitación
    pendiente la referencia todavía (data-model.md §Rol, estrategia de
    rollback) — si alguno la referencia, se deja la fila para no dejar un
    `role_id` huérfano en `shared.users`/`shared.user_invitations`."""
    bind = op.get_bind()
    in_use = bind.execute(
        text(
            "SELECT 1 FROM shared.roles r "
            "WHERE r.name = :name AND ("
            "  EXISTS (SELECT 1 FROM shared.users u WHERE u.role_id = r.id)"
            "  OR EXISTS (SELECT 1 FROM shared.user_invitations i WHERE i.role_id = r.id)"
            ")"
        ),
        {"name": _ROLE_NAME},
    ).scalar()
    if in_use:
        return
    bind.execute(
        text("DELETE FROM shared.roles WHERE name = :name"),
        {"name": _ROLE_NAME},
    )
