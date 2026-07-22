"""cash module redesign: movement kind+category, payment_method type, shift close_note

Revision ID: b1c2d3e4f5a6
Revises: abd505aae914
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'abd505aae914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    """Los esquemas de scratch (p. ej. tenant_default) pueden no tener las
    tablas base; se saltan para no fallar el ALTER."""
    return op.get_bind().execute(
        text("SELECT to_regclass(:qname)"), {"qname": f"{schema}.{table}"}
    ).scalar() is not None


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    """Upgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)

    if not _has_table(schema, "cash_movements"):
        return

    # --- cash_movements: type -> kind + category ---
    op.drop_constraint(
        "ck__cash_movements__ck_cash_movement_type",
        "cash_movements",
        schema=schema,
    )
    op.alter_column(
        "cash_movements",
        "type",
        new_column_name="kind",
        type_=sa.String(length=20),
        existing_nullable=False,
        schema=schema,
    )
    op.add_column(
        "cash_movements",
        sa.Column("category", sa.String(length=100), nullable=True),
        schema=schema,
    )
    op.add_column(
        "cash_movements",
        sa.Column("user_name", sa.String(length=255), nullable=True),
        schema=schema,
    )
    # description pasa a opcional (la categoría es ahora el descriptor requerido).
    op.alter_column(
        "cash_movements", "description", existing_type=sa.String(length=255),
        nullable=True, schema=schema,
    )
    # Backfill: in -> ingreso, out -> egreso (no había retiros previos).
    op.execute(f"UPDATE {schema_quoted}.cash_movements SET kind = 'ingreso' WHERE kind = 'in'")
    op.execute(f"UPDATE {schema_quoted}.cash_movements SET kind = 'egreso' WHERE kind = 'out'")
    op.create_check_constraint(
        "ck_cash_movement_kind",
        "cash_movements",
        "kind IN ('ingreso', 'egreso', 'retiro')",
        schema=schema,
    )

    # --- payment_methods: + type (backfill desde is_cash) ---
    op.add_column(
        "payment_methods",
        sa.Column("type", sa.String(length=20), nullable=False, server_default="other"),
        schema=schema,
    )
    op.execute(f"UPDATE {schema_quoted}.payment_methods SET type = 'cash' WHERE is_cash = true")
    op.create_check_constraint(
        "ck_payment_method_type",
        "payment_methods",
        "type IN ('cash', 'card', 'transfer', 'other')",
        schema=schema,
    )

    # --- cash_shifts: + close_note ---
    op.add_column(
        "cash_shifts",
        sa.Column("close_note", sa.String(length=500), nullable=True),
        schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    """Downgrade schema."""
    preparer = sa.sql.compiler.IdentifierPreparer(op.get_bind().dialect)
    schema_quoted = preparer.format_schema(schema)

    if not _has_table(schema, "cash_movements"):
        return

    # --- cash_shifts ---
    op.drop_column("cash_shifts", "close_note", schema=schema)

    # --- payment_methods ---
    op.drop_constraint(
        "ck__payment_methods__ck_payment_method_type",
        "payment_methods",
        schema=schema,
    )
    op.drop_column("payment_methods", "type", schema=schema)

    # --- cash_movements: kind -> type ---
    op.drop_constraint(
        "ck__cash_movements__ck_cash_movement_kind",
        "cash_movements",
        schema=schema,
    )
    # Revertir backfill: ingreso -> in, egreso/retiro -> out.
    op.execute(f"UPDATE {schema_quoted}.cash_movements SET kind = 'in' WHERE kind = 'ingreso'")
    op.execute(f"UPDATE {schema_quoted}.cash_movements SET kind = 'out' WHERE kind IN ('egreso', 'retiro')")
    op.execute(f"UPDATE {schema_quoted}.cash_movements SET description = '' WHERE description IS NULL")
    op.alter_column(
        "cash_movements", "description", existing_type=sa.String(length=255),
        nullable=False, schema=schema,
    )
    op.drop_column("cash_movements", "user_name", schema=schema)
    op.drop_column("cash_movements", "category", schema=schema)
    op.alter_column(
        "cash_movements",
        "kind",
        new_column_name="type",
        type_=sa.String(length=10),
        existing_nullable=False,
        schema=schema,
    )
    op.create_check_constraint(
        "ck_cash_movement_type",
        "cash_movements",
        "type IN ('in', 'out')",
        schema=schema,
    )
