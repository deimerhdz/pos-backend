"""fix inventory and inventory_movements FK to point to per-schema products

Revision ID: a1b2c3d4e5f6
Revises: 4b168d55a245
Create Date: 2026-06-07 16:00:00.000000

Repara los FK de `inventory` e `inventory_movements` que quedaron apuntando al
literal `tenant_default.products` en todos los esquemas de tenant (bug de las
migraciones 752fe2608b73 y 885511c80053). Los recrea apuntando al `products` del
mismo esquema.
"""
from typing import Sequence, Union

from app.scripts.tenant import for_each_tenant_schema
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '4b168d55a245'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f("fk__inventory__product_id__products"),
        "inventory", schema=schema, type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk__inventory__product_id__products"),
        "inventory", "products", ["product_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )

    op.drop_constraint(
        op.f("fk__inventory_movements__product_id__products"),
        "inventory_movements", schema=schema, type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk__inventory_movements__product_id__products"),
        "inventory_movements", "products", ["product_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema (restaura el estado anterior apuntando a tenant_default)."""
    op.drop_constraint(
        op.f("fk__inventory__product_id__products"),
        "inventory", schema=schema, type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk__inventory__product_id__products"),
        "inventory", "products", ["product_id"], ["id"],
        source_schema=schema, referent_schema="tenant_default",
    )

    op.drop_constraint(
        op.f("fk__inventory_movements__product_id__products"),
        "inventory_movements", schema=schema, type_="foreignkey",
    )
    op.create_foreign_key(
        op.f("fk__inventory_movements__product_id__products"),
        "inventory_movements", "products", ["product_id"], ["id"],
        source_schema=schema, referent_schema="tenant_default",
    )
