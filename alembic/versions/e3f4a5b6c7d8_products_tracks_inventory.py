"""control de inventario por producto: products.tracks_inventory

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return

    op.add_column("products", sa.Column("tracks_inventory", sa.Boolean(), nullable=False,
                                        server_default="true"), schema=schema)

    # Backfill dirigido (spec 027, FR-010/FR-011): todo producto que ya existía
    # migra activado si ya superaba RN-CAT-34 (spec 003) -- alguna presentación
    # activa con receta fija o un grupo que efectivamente descuenta -- y apagado
    # si no tenía nada configurado (y por tanto, hoy, no se puede vender). Replica
    # en SQL la lógica de load_recipe/group_discounts
    # (app/catalog_engine/consumption.py:50-88).
    op.execute(f"""
        UPDATE {schema}.products p
        SET tracks_inventory = EXISTS (
            SELECT 1 FROM {schema}.product_variants pv
            WHERE pv.product_id = p.id AND pv.active = true AND (
                EXISTS (
                    SELECT 1 FROM {schema}.recipe_items ri
                    WHERE ri.product_variant_id = pv.id
                )
                OR EXISTS (
                    SELECT 1 FROM {schema}.variant_option_groups vog
                    WHERE vog.product_variant_id = pv.id AND vog.quantity_per_option > 0
                )
                OR EXISTS (
                    SELECT 1
                    FROM {schema}.variant_option_groups vog
                    JOIN {schema}.options o ON o.option_group_id = vog.option_group_id
                    WHERE vog.product_variant_id = pv.id
                      AND o.active = true
                      AND o.inventory_item_id IS NOT NULL
                      AND o.item_quantity > 0
                )
            )
        )
    """)


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "products"):
        return
    op.drop_column("products", "tracks_inventory", schema=schema)
