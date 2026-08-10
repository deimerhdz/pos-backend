"""promotion_targets: precio y tamaño de paquete por target

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-10

Una promoción `qty_price` tenía un solo `min_qty` y un solo `value` para todo su
alcance: con la categoría «Ensaladas de frutas» a «2 por $12.000», la Grande
(normal $16.000) y la Pequeña (normal $9.000) quedaban las dos al mismo precio.
Ahora cada target puede traer los suyos, y el valor de la promoción es el
defecto de quien no los define.

Los índices únicos parciales no existían y hasta hoy daba igual: un target
repetido producía el mismo resultado. En cuanto lleva precio, dos filas del
mismo producto con precios distintos son ambiguas y el descuento pasaría a
depender del orden que devuelva el SELECT.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
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

    op.add_column(
        "promotion_targets",
        sa.Column("value", sa.Numeric(12, 2), nullable=True),
        schema=schema,
    )
    op.add_column(
        "promotion_targets",
        sa.Column("min_qty", sa.Integer(), nullable=True),
        schema=schema,
    )
    op.create_check_constraint(
        op.f("ck__promotion_targets__ck_target_value_positive"),
        "promotion_targets",
        "value IS NULL OR value >= 0",
        schema=schema,
    )
    # Un paquete de 1 es un precio, no una promoción: misma regla que `promotions`.
    op.create_check_constraint(
        op.f("ck__promotion_targets__ck_target_pack_size"),
        "promotion_targets",
        "min_qty IS NULL OR min_qty >= 2",
        schema=schema,
    )

    # Deduplicar antes de los índices únicos, conservando la fila más antigua.
    for columna in ("product_id", "category_id"):
        borradas = op.get_bind().execute(
            text(
                f"""
                DELETE FROM {schema}.promotion_targets t
                USING {schema}.promotion_targets keep
                WHERE t.{columna} IS NOT NULL
                  AND t.promotion_id = keep.promotion_id
                  AND t.{columna} = keep.{columna}
                  AND t.ctid > keep.ctid
                RETURNING t.id
                """
            )
        ).fetchall()
        if borradas:
            print(
                f"[{schema}] {len(borradas)} target(s) duplicado(s) por {columna} "
                "eliminados antes de crear el índice único."
            )

    op.create_index(
        "uq_promotion_targets_product",
        "promotion_targets",
        ["promotion_id", "product_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_promotion_targets_category",
        "promotion_targets",
        ["promotion_id", "category_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("category_id IS NOT NULL"),
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotion_targets"):
        return

    op.drop_index("uq_promotion_targets_category", "promotion_targets", schema=schema)
    op.drop_index("uq_promotion_targets_product", "promotion_targets", schema=schema)
    op.drop_constraint(
        op.f("ck__promotion_targets__ck_target_pack_size"),
        "promotion_targets",
        schema=schema,
        type_="check",
    )
    op.drop_constraint(
        op.f("ck__promotion_targets__ck_target_value_positive"),
        "promotion_targets",
        schema=schema,
        type_="check",
    )
    op.drop_column("promotion_targets", "min_qty", schema=schema)
    op.drop_column("promotion_targets", "value", schema=schema)
