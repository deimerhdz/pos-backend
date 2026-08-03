"""variant_option_groups: grupos, cardinalidad y consumo por VARIANTE

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-08-03 00:00:00.000000

Cada tamaño define cuántas opciones elige el cliente y cuánto descuenta cada una:
la ensalada pequeña elige 1 sabor y descuenta 60 g, la mediana elige 2 y descuenta
120 g de cada uno.

Antes eso estaba partido y era inexpresable:
  - `product_option_groups.min/max_select` → por PRODUCTO, igual para todos los tamaños
  - `recipe_items.option_group_id` (el "slot") → la cantidad, por variante

Esta migración las funde en `variant_option_groups`, al nivel de la variante.

**Sin cambio de comportamiento**: el backfill copia min/max a todas las variantes
activas con `quantity_per_option = 0`, así que el consumo lo sigue aportando
`options.item_quantity` exactamente igual que antes. Los slots de `recipe_items` se
eliminan sin backfill porque no hay ninguno (se verificó: 0 filas en los 3 tenants).

⚠ El downgrade pierde la configuración por tamaño: al volver a un único min/max por
producto se queda el de la primera variante, y `quantity_per_option` desaparece.
"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    if not _has_table(schema, "product_option_groups"):
        return

    op.create_table(
        "variant_option_groups",
        sa.Column("product_variant_id", sa.UUID(), nullable=False),
        sa.Column("option_group_id", sa.UUID(), nullable=False),
        sa.Column("min_select", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_select", sa.Integer(), server_default="1", nullable=False),
        sa.Column("quantity_per_option", sa.Numeric(12, 3), server_default="0", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "min_select >= 0",
            name=op.f("ck__variant_option_groups__ck_variant_option_group_min_select"),
        ),
        sa.CheckConstraint(
            "max_select >= min_select",
            name=op.f("ck__variant_option_groups__ck_variant_option_group_max_ge_min"),
        ),
        sa.CheckConstraint(
            "quantity_per_option >= 0",
            name=op.f("ck__variant_option_groups__ck_variant_option_group_qty_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], [f"{schema}.product_variants.id"],
            name=op.f("fk__variant_option_groups__product_variant_id__product_variants"),
            ondelete="CASCADE",
        ),
        # Sin ondelete: los grupos se retiran por soft-delete, así que un DELETE físico
        # debe fallar en vez de dejar variantes vendiendo sin descontar.
        sa.ForeignKeyConstraint(
            ["option_group_id"], [f"{schema}.option_groups.id"],
            name=op.f("fk__variant_option_groups__option_group_id__option_groups"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__variant_option_groups")),
        sa.UniqueConstraint(
            "product_variant_id", "option_group_id",
            name="uq__variant_option_groups__product_variant_id__option_group_id",
        ),
        schema=schema,
    )
    op.create_index(
        op.f("ix__variant_option_groups__product_variant_id"), "variant_option_groups",
        ["product_variant_id"], schema=schema,
    )
    op.create_index(
        op.f("ix__variant_option_groups__option_group_id"), "variant_option_groups",
        ["option_group_id"], schema=schema,
    )

    # Backfill: lo que era del producto pasa a cada una de sus variantes activas.
    # `quantity_per_option = 0` mantiene el consumo actual, que sale de
    # `options.item_quantity`; el dueño ajusta después la cantidad por tamaño.
    op.execute(text(f'''
        INSERT INTO "{schema}".variant_option_groups
            (id, product_variant_id, option_group_id, min_select, max_select, quantity_per_option)
        SELECT gen_random_uuid(), pv.id, pog.option_group_id,
               pog.min_select, pog.max_select, 0
        FROM "{schema}".product_option_groups pog
        JOIN "{schema}".product_variants pv
          ON pv.product_id = pog.product_id AND pv.active
    '''))

    op.drop_table("product_option_groups", schema=schema)

    # --- recipe_items vuelve a ser solo insumos fijos ---
    # No hay slots que migrar (0 filas verificadas); el DELETE es una red por si algún
    # entorno los creó después de comprobarlo.
    op.execute(text(
        f'DELETE FROM "{schema}".recipe_items WHERE option_group_id IS NOT NULL'
    ))
    op.drop_constraint(
        op.f("ck__recipe_items__ck_recipe_item_target_xor"),
        "recipe_items", type_="check", schema=schema,
    )
    op.drop_index("uq__recipe_items__variant__option_group",
                  table_name="recipe_items", schema=schema)
    op.drop_index("uq__recipe_items__variant__inventory_item",
                  table_name="recipe_items", schema=schema)
    op.alter_column("recipe_items", "inventory_item_id",
                    existing_type=sa.UUID(), nullable=False, schema=schema)
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


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "variant_option_groups"):
        return

    # --- recipe_items recupera la columna de slot (vacía) ---
    op.add_column("recipe_items", sa.Column("option_group_id", sa.UUID(), nullable=True),
                  schema=schema)
    op.create_index(op.f("ix__recipe_items__option_group_id"), "recipe_items",
                    ["option_group_id"], schema=schema)
    op.create_foreign_key(
        op.f("fk__recipe_items__option_group_id__option_groups"),
        "recipe_items", "option_groups", ["option_group_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )
    op.drop_constraint("uq__recipe_items__product_variant_id__inventory_item_id",
                       "recipe_items", type_="unique", schema=schema)
    op.alter_column("recipe_items", "inventory_item_id",
                    existing_type=sa.UUID(), nullable=True, schema=schema)
    op.create_index("uq__recipe_items__variant__inventory_item", "recipe_items",
                    ["product_variant_id", "inventory_item_id"], unique=True,
                    postgresql_where=sa.text("inventory_item_id IS NOT NULL"), schema=schema)
    op.create_index("uq__recipe_items__variant__option_group", "recipe_items",
                    ["product_variant_id", "option_group_id"], unique=True,
                    postgresql_where=sa.text("option_group_id IS NOT NULL"), schema=schema)
    op.create_check_constraint(
        "ck_recipe_item_target_xor", "recipe_items",
        "num_nonnulls(inventory_item_id, option_group_id) = 1", schema=schema,
    )

    op.create_table(
        "product_option_groups",
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("option_group_id", sa.UUID(), nullable=False),
        sa.Column("min_select", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_select", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "max_select >= min_select",
            name=op.f("ck__product_option_groups__ck_product_option_group_max_ge_min"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], [f"{schema}.products.id"],
            name=op.f("fk__product_option_groups__product_id__products"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["option_group_id"], [f"{schema}.option_groups.id"],
            name=op.f("fk__product_option_groups__option_group_id__option_groups"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk__product_option_groups")),
        sa.UniqueConstraint(
            "product_id", "option_group_id",
            name="uq__product_option_groups__product_id__option_group_id",
        ),
        schema=schema,
    )
    op.create_index(op.f("ix__product_option_groups__product_id"), "product_option_groups",
                    ["product_id"], schema=schema)
    op.create_index(op.f("ix__product_option_groups__option_group_id"), "product_option_groups",
                    ["option_group_id"], schema=schema)

    # Colapsa a un min/max por producto: se queda el de la primera variante y se pierde
    # la diferencia entre tamaños (por eso el downgrade no es reversible sin pérdida).
    op.execute(text(f'''
        INSERT INTO "{schema}".product_option_groups
            (id, product_id, option_group_id, min_select, max_select)
        SELECT DISTINCT ON (pv.product_id, vog.option_group_id)
               gen_random_uuid(), pv.product_id, vog.option_group_id,
               vog.min_select, vog.max_select
        FROM "{schema}".variant_option_groups vog
        JOIN "{schema}".product_variants pv ON pv.id = vog.product_variant_id
        ORDER BY pv.product_id, vog.option_group_id, pv.created_at
    '''))

    op.drop_index(op.f("ix__variant_option_groups__option_group_id"),
                  table_name="variant_option_groups", schema=schema)
    op.drop_index(op.f("ix__variant_option_groups__product_variant_id"),
                  table_name="variant_option_groups", schema=schema)
    op.drop_table("variant_option_groups", schema=schema)
