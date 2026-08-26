"""catálogo de métodos de pago: siembra Efectivo/Nequi/Transferencia Bancolombia

Revision ID: 130642d23e76
Revises: a241d5c311bd
Create Date: 2026-08-24 00:00:00.000002

"""
import json
import uuid
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = '130642d23e76'
down_revision: Union[str, Sequence[str], None] = 'a241d5c311bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# data-model.md §Migración, paso 1. `fields` sigue la forma de
# `PaymentMethodFieldDefinition` (spec 032, contracts/super-admin-catalog.md).
_SEED = [
    {"name": "Efectivo", "type": "cash", "fields": []},
    {
        "name": "Nequi", "type": "transfer",
        "fields": [
            {"key": "celular", "label": "Número de celular", "required": True,
             "format": "numeric", "length": 10},
            {"key": "qr", "label": "Código QR", "required": False, "format": "image"},
        ],
    },
    {
        "name": "Transferencia Bancolombia", "type": "transfer",
        "fields": [
            {"key": "cuenta", "label": "Número de cuenta", "required": True, "format": "text"},
            {"key": "tipo_cuenta", "label": "Tipo de cuenta", "required": True, "format": "text"},
            {"key": "qr", "label": "Código QR", "required": False, "format": "image"},
        ],
    },
]


def upgrade() -> None:
    """Siembra el catálogo con los tres métodos que los tenants ya usan hoy
    (FR-015). Idempotente por `name` — no reinserta si ya existe (permite
    reejecutar esta migración en un entorno donde el Super Admin ya haya
    creado alguno de estos tres manualmente vía la API antes de llegar aquí)."""
    bind = op.get_bind()
    for entry in _SEED:
        exists = bind.execute(
            text("SELECT 1 FROM shared.payment_method_catalog WHERE name = :name"),
            {"name": entry["name"]},
        ).scalar()
        if exists:
            continue
        bind.execute(
            text(
                "INSERT INTO shared.payment_method_catalog "
                "(id, name, type, active, fields, created_at) "
                "VALUES (:id, :name, :type, true, :fields, now())"
            ),
            {
                "id": uuid.uuid4(),
                "name": entry["name"],
                "type": entry["type"],
                "fields": json.dumps(entry["fields"]),
            },
        )


def downgrade() -> None:
    """Borra únicamente las tres filas sembradas por nombre (Principio VIII:
    estrategia de rollback explícita) — no toca ninguna otra entrada de
    catálogo que el Super Admin haya creado después."""
    bind = op.get_bind()
    for entry in _SEED:
        bind.execute(
            text("DELETE FROM shared.payment_method_catalog WHERE name = :name"),
            {"name": entry["name"]},
        )
