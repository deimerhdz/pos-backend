"""promotions: estados, prioridad, descripcion, qty_price y FK de venta

Revision ID: a1b2c3d4e5f6
Revises: c5d6e7f8a9b0
Create Date: 2026-08-07

Migración de datos:
  active=true  -> status='active'
  active=false -> status='paused'
Lo ya vencido (`ends_at` anterior a hoy) pasa directo a 'finished'.

`buy_x_get_y` sale del dominio porque descontaba 0. Si existiera alguna fila con
ese tipo, la migración falla a propósito: hay que decidir a mano en qué se
convierte, no silenciarla.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    huerfanas = op.get_bind().execute(
        text(f"SELECT count(*) FROM {schema}.promotions WHERE type = 'buy_x_get_y'")
    ).scalar()
    if huerfanas:
        raise RuntimeError(
            f"[{schema}] {huerfanas} promoción(es) type='buy_x_get_y'. Nunca descontaron "
            "nada. Conviértelas o bórralas antes de migrar."
        )

    op.add_column("promotions", sa.Column("description", sa.Text(), nullable=True), schema=schema)
    op.add_column("promotions", sa.Column("priority", sa.Integer(), nullable=False,
                                          server_default="0"), schema=schema)
    op.add_column("promotions", sa.Column("status", sa.String(length=16), nullable=False,
                                          server_default="draft"), schema=schema)

    op.execute(f"UPDATE {schema}.promotions "
               "SET status = CASE WHEN active THEN 'active' ELSE 'paused' END")
    op.execute(f"UPDATE {schema}.promotions SET status = 'finished' "
               "WHERE ends_at IS NOT NULL AND ends_at::date < CURRENT_DATE")

    op.drop_column("promotions", "active", schema=schema)
    op.drop_column("promotions", "buy_qty", schema=schema)
    op.drop_column("promotions", "get_qty", schema=schema)

    op.create_index(op.f("ix__promotions__status"), "promotions", ["status"], schema=schema)
    op.create_index("ix_promotions_status_ends_at", "promotions", ["status", "ends_at"],
                    schema=schema)

    op.drop_constraint(op.f("ck__promotions__ck_promotion_type"), "promotions",
                       schema=schema, type_="check")
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'combo', 'qty_price')", schema=schema,
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_status"), "promotions",
        "status IN ('draft', 'active', 'paused', 'finished')", schema=schema,
    )
    # Lo que `PromotionUpdate` no validaba: un PATCH con value=500 sobre un
    # percent dejaba la caja sin poder cobrar esa categoría.
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_percent_range"), "promotions",
        "type <> 'percent' OR value <= 100", schema=schema,
    )
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_qty_price_pack"), "promotions",
        "type <> 'qty_price' OR min_qty >= 2", schema=schema,
    )

    # H-1: borrar una promoción ya vendida lanzaba IntegrityError -> 500.
    if _has_table(schema, "sales"):
        op.drop_constraint(op.f("fk__sales__promotion_id__promotions"), "sales",
                           schema=schema, type_="foreignkey")
        op.create_foreign_key(
            op.f("fk__sales__promotion_id__promotions"), "sales", "promotions",
            ["promotion_id"], ["id"], source_schema=schema, referent_schema=schema,
            ondelete="SET NULL",
        )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "promotions"):
        return

    if _has_table(schema, "sales"):
        op.drop_constraint(op.f("fk__sales__promotion_id__promotions"), "sales",
                           schema=schema, type_="foreignkey")
        op.create_foreign_key(
            op.f("fk__sales__promotion_id__promotions"), "sales", "promotions",
            ["promotion_id"], ["id"], source_schema=schema, referent_schema=schema,
        )

    for name in ("ck__promotions__ck_promotion_qty_price_pack",
                 "ck__promotions__ck_promotion_percent_range",
                 "ck__promotions__ck_promotion_status",
                 "ck__promotions__ck_promotion_type"):
        op.drop_constraint(op.f(name), "promotions", schema=schema, type_="check")
    op.create_check_constraint(
        op.f("ck__promotions__ck_promotion_type"), "promotions",
        "type IN ('percent', 'fixed', 'buy_x_get_y', 'combo', 'qty_price')", schema=schema,
    )

    op.drop_index("ix_promotions_status_ends_at", "promotions", schema=schema)
    op.drop_index(op.f("ix__promotions__status"), "promotions", schema=schema)

    op.add_column("promotions", sa.Column("get_qty", sa.Integer(), nullable=True), schema=schema)
    op.add_column("promotions", sa.Column("buy_qty", sa.Integer(), nullable=True), schema=schema)
    op.add_column("promotions", sa.Column("active", sa.Boolean(), nullable=False,
                                          server_default="true"), schema=schema)
    op.execute(f"UPDATE {schema}.promotions SET active = (status = 'active')")

    op.drop_column("promotions", "status", schema=schema)
    op.drop_column("promotions", "priority", schema=schema)
    op.drop_column("promotions", "description", schema=schema)
