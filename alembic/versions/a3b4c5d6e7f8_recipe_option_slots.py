"""recipe_items: líneas dinámicas ("slots") ligadas a un grupo de opciones

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-01 00:00:00.000000

Una línea de receta pasa a ser de uno de dos tipos, mutuamente excluyentes:

  - fija:  inventory_item_id set, option_group_id NULL  → lo de siempre (200 g de fruta)
  - slot:  option_group_id set, inventory_item_id NULL  → "1 bola del grupo Sabores";
           el insumo lo resuelve la opción que elija el comensal, y `quantity` es la
           cantidad por cada opción elegida.

Así la cantidad consumida puede variar por producto y por tamaño (vive en la receta
de la variante) en vez de ser global a la opción (`options.item_quantity`).

Sin backfill: toda fila existente tiene inventory_item_id NOT NULL y option_group_id
NULL, así que satisface el XOR por construcción.

⚠ El downgrade DESTRUYE los slots (DELETE de las filas con option_group_id).
"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "recipe_items"):
        return

    op.add_column(
        "recipe_items", sa.Column("option_group_id", sa.UUID(), nullable=True), schema=schema
    )
    op.create_index(
        op.f("ix__recipe_items__option_group_id"), "recipe_items",
        ["option_group_id"], unique=False, schema=schema,
    )
    # Sin ondelete: los grupos se retiran por soft-delete (active=false), así que un
    # DELETE físico debe fallar en vez de vaciar recetas en silencio.
    op.create_foreign_key(
        op.f("fk__recipe_items__option_group_id__option_groups"),
        "recipe_items", "option_groups", ["option_group_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )

    op.alter_column(
        "recipe_items", "inventory_item_id",
        existing_type=sa.UUID(), nullable=True, schema=schema,
    )

    # La unique original deja de proteger nada en cuanto haya NULLs (Postgres los
    # considera distintos entre sí): se podrían insertar N slots del mismo grupo en la
    # misma variante. La sustituyen dos índices únicos parciales, uno por tipo de línea.
    op.drop_constraint(
        "uq__recipe_items__product_variant_id__inventory_item_id",
        "recipe_items", type_="unique", schema=schema,
    )
    op.create_index(
        "uq__recipe_items__variant__inventory_item", "recipe_items",
        ["product_variant_id", "inventory_item_id"], unique=True,
        postgresql_where=sa.text("inventory_item_id IS NOT NULL"), schema=schema,
    )
    op.create_index(
        "uq__recipe_items__variant__option_group", "recipe_items",
        ["product_variant_id", "option_group_id"], unique=True,
        postgresql_where=sa.text("option_group_id IS NOT NULL"), schema=schema,
    )

    # XOR: una fila con ambos NULL no consumiría nada y sería indetectable; con ambos
    # set sería ambigua.
    op.create_check_constraint(
        "ck_recipe_item_target_xor", "recipe_items",
        "num_nonnulls(inventory_item_id, option_group_id) = 1", schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "recipe_items"):
        return

    op.drop_constraint(
        op.f("ck__recipe_items__ck_recipe_item_target_xor"),
        "recipe_items", type_="check", schema=schema,
    )
    op.drop_index("uq__recipe_items__variant__option_group",
                  table_name="recipe_items", schema=schema)
    op.drop_index("uq__recipe_items__variant__inventory_item",
                  table_name="recipe_items", schema=schema)

    # Los slots no tienen representación en el modelo anterior: se pierden.
    op.execute(text(
        f'DELETE FROM "{schema}".recipe_items WHERE option_group_id IS NOT NULL'
    ))

    op.alter_column(
        "recipe_items", "inventory_item_id",
        existing_type=sa.UUID(), nullable=False, schema=schema,
    )
    op.create_unique_constraint(
        "uq__recipe_items__product_variant_id__inventory_item_id",
        "recipe_items", ["product_variant_id", "inventory_item_id"], schema=schema,
    )

    op.drop_constraint(
        op.f("fk__recipe_items__option_group_id__option_groups"),
        "recipe_items", type_="foreignkey", schema=schema,
    )
    op.drop_index(op.f("ix__recipe_items__option_group_id"),
                  table_name="recipe_items", schema=schema)
    op.drop_column("recipe_items", "option_group_id", schema=schema)
