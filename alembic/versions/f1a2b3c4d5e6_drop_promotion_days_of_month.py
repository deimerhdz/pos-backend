"""promotions: retirar days_of_month

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-10

`days_of_month` restringía una promoción a fechas del calendario ("solo el 15 y
el 30"). Funcionaba de punta a punta, pero el formulario nunca mostró la
restricción en su vista previa ni en la columna de vigencia: una promoción del
15 y el 30 se anunciaba como "todos los días". Eso, más que su preset "fin de
mes" significara en realidad "día 31" (inexistente en 5 de los 12 meses) y que
se combinara con `days_of_week` mediante un Y que podía dejarla sin activarse
nunca, pesó más que su único caso de uso real, la quincena.

**El dato se pierde y el comportamiento cambia.** Una promoción restringida al
15 y al 30 pasa a aplicar los 30 días del mes. Es una decisión tomada, no un
descuido: la migración no aborta, pero deja en el log el nombre de cada
promoción afectada para que el cambio quede rastreable en el despliegue.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    """`for_each_tenant_schema` también recorre `tenant_default`, que puede no
    tener todavía la tabla."""
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    afectadas = op.get_bind().execute(
        text(
            f"SELECT name, days_of_month FROM {schema}.promotions "
            "WHERE days_of_month IS NOT NULL"
        )
    ).fetchall()
    for name, dias in afectadas:
        print(
            f"[{schema}] '{name}' estaba restringida a los días {dias} del mes; "
            "a partir de ahora aplicará todos los días."
        )

    op.drop_column("promotions", "days_of_month", schema=schema)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    # Misma definición con la que la creó `a63ddb1f0d97`. Recupera la columna,
    # no el dato: las restricciones borradas en el upgrade no vuelven.
    op.add_column(
        "promotions",
        sa.Column("days_of_month", sa.String(length=100), nullable=True),
        schema=schema,
    )
