"""qty_price: bajar el precio de la promoción a cada uno de sus destinos

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-08-10

El formulario dejó de pedir un "paquete por defecto": en un `qty_price` el
precio y las unidades viven en cada destino. A partir de ahora `_pack_terms`
**no cae** al valor de la promoción — un destino sin precio no descuenta — para
que el campo muerto no se convierta en un 100 % de descuento.

Esta migración es lo que evita que ese cambio apague promociones de producción
en silencio: copia a cada destino lo que hasta hoy heredaba.

Las promociones `qty_price` **sin destinos** no se pueden arreglar aquí (no hay
fila donde poner el precio) y dejarán de descontar. Se listan por nombre en el
log del despliegue para que alguien decida qué hacer con ellas.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
from sqlalchemy import text


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotion_targets"):
        return

    copiadas = op.get_bind().execute(
        text(
            f"""
            UPDATE {schema}.promotion_targets t
            SET value = COALESCE(t.value, p.value),
                min_qty = COALESCE(t.min_qty, GREATEST(p.min_qty, 2))
            FROM {schema}.promotions p
            WHERE p.id = t.promotion_id
              AND p.type = 'qty_price'
              AND (t.value IS NULL OR t.min_qty IS NULL)
            RETURNING t.id
            """
        )
    ).fetchall()
    if copiadas:
        print(f"[{schema}] {len(copiadas)} destino(s) heredaron el precio de su promoción.")

    huerfanas = op.get_bind().execute(
        text(
            f"""
            SELECT p.name FROM {schema}.promotions p
            WHERE p.type = 'qty_price'
              AND NOT EXISTS (
                SELECT 1 FROM {schema}.promotion_targets t WHERE t.promotion_id = p.id
              )
            """
        )
    ).fetchall()
    for (nombre,) in huerfanas:
        print(
            f"[{schema}] AVISO: el paquete '{nombre}' no tiene productos ni categorías. "
            "Ya no descuenta: el precio se define por destino. Revísalo o finalízalo."
        )


def downgrade(schema: str = None) -> None:
    """No-op: los precios copiados son válidos también en el modelo anterior,
    donde el destino simplemente pisaba al de la promoción."""
