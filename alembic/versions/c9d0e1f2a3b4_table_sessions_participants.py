"""Sesión de mesa: table_sessions + session_participants (renombre de dining_sessions)

Introduce el agrupador que faltaba. Antes la "sesión" era por comensal
(`dining_sessions`) y la agrupación por mesa era implícita, así que no había
dónde poner un `billing_mode`, un `closed_by` ni un timeout de mesa.

Cambios:
  1. nueva `table_sessions` (una activa por mesa, índice parcial);
  2. `dining_sessions` → `session_participants` (+ `display_name`, `display_label`,
     `joined_at`, `table_session_id`);
  3. renombre de las FK que la apuntaban a `participant_id`;
  4. `customer_orders`/`sales` cuelgan de `table_session_id`;
  5. se elimina `idx_open_order_per_table` — una mesa puede tener varios pedidos
     simultáneos (uno por comensal, o varias rondas del mismo);
  6. nuevo estado `recibida` en `ck_customer_order_status` (pedido enviado por el
     comensal, aún sin descontar stock);
  7. `order_cancel_logs.user_id` pasa a nullable + `participant_id` (el comensal
     puede cancelar sus pedidos tempranos y no es un usuario del sistema).

**El backfill del paso 2 no es reversible sin pérdida.** Los participantes
históricos ya cerrados no se pueden reagrupar en las mesas que compartieron
(esa información nunca se guardó), así que se les asigna una `table_session`
propia. `downgrade()` deshace la estructura pero no reconstruye ese agrupamiento.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union
from app.scripts.tenant import for_each_tenant_schema
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(schema: str, table: str) -> bool:
    return op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{table}"}
    ).scalar() is not None


# El repo usa naming_convention (app/core/models.py): los constraints se llaman
# `ck__<tabla>__<nombre>`, `fk__<tabla>__<cols>__<tabla_ref>`, `pk__<tabla>`, y los
# índices de `index=True` quedan como `ix_tenant_<tabla>_<col>` (el schema lógico
# es "tenant"). Renombrar una tabla o una columna NO actualiza esos nombres, así
# que hay que hacerlo a mano o el autogenerate posterior propondrá recrearlos.
#
# Ojo con la asimetría: la plantilla de `ck` incluye %(constraint_name)s, así que
# SQLAlchemy **envuelve** el nombre que se le pase (hay que dar el nombre lógico,
# `ck_foo`, y sale `ck__tabla__ck_foo`). La de `fk` no tiene ese token y solo se
# aplica cuando el nombre es None, así que a los FK hay que darles ya el nombre
# completo. Los índices siempre usan el literal.

def _rename_constraint(schema: str, table: str, old: str, new: str) -> None:
    """Renombra un constraint solo si existe (los schemas de tenants viejos pueden
    diferir)."""
    exists = op.get_bind().execute(text("""
        SELECT 1 FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s AND c.relname = :t AND con.conname = :o
    """), {"s": schema, "t": table, "o": old}).scalar()
    if exists:
        op.execute(text(
            f'ALTER TABLE "{schema}".{table} RENAME CONSTRAINT {old} TO {new}'
        ))


def _rename_index(schema: str, old: str, new: str) -> None:
    exists = op.get_bind().execute(
        text("SELECT to_regclass(:q)"), {"q": f"{schema}.{old}"}
    ).scalar()
    if exists:
        op.execute(text(f'ALTER INDEX "{schema}".{old} RENAME TO {new}'))


@for_each_tenant_schema
def upgrade(schema: str) -> None:
    # Los schemas de tenants viejos pueden no tener las tablas base todavía
    # (se crean por metadata.create_all al dar de alta el tenant).
    if not _has_table(schema, "dining_sessions"):
        return

    bind = op.get_bind()

    # --- 1. table_sessions -----------------------------------------------
    op.create_table(
        "table_sessions",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("dining_table_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("opened_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_by_user_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_by_user_name", sa.String(255), nullable=True),
        sa.Column("billing_mode", sa.String(10), nullable=True),
        sa.ForeignKeyConstraint(["dining_table_id"], [f"{schema}.dining_tables.id"],
                                name="fk__table_sessions__dining_table_id__dining_tables"),
        sa.CheckConstraint("status IN ('active', 'closed')",
                           name="ck_table_session_status"),
        sa.CheckConstraint(
            "billing_mode IS NULL OR billing_mode IN ('unified', 'split')",
            name="ck_table_session_billing_mode",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_tenant_table_sessions_dining_table_id", "table_sessions", ["dining_table_id"],
        schema=schema,
    )
    # Invariante central: una sola sesión activa por mesa. Escanear el QR de una
    # mesa ocupada une a la sesión en curso en vez de abrir otra.
    op.create_index(
        "idx_active_session_per_table", "table_sessions", ["dining_table_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"), schema=schema,
    )

    # --- 2. dining_sessions → session_participants ------------------------
    op.rename_table("dining_sessions", "session_participants", schema=schema)
    op.alter_column("session_participants", "customer_name",
                    new_column_name="display_name", schema=schema)
    op.alter_column("session_participants", "opened_at",
                    new_column_name="joined_at", schema=schema)
    op.add_column("session_participants",
                  sa.Column("display_label", sa.String(255), nullable=True), schema=schema)
    op.add_column("session_participants",
                  sa.Column("table_session_id", sa.UUID(as_uuid=True), nullable=True),
                  schema=schema)

    _rename_constraint(schema, "session_participants",
                       "ck__dining_sessions__ck_dining_session_status",
                       "ck__session_participants__ck_session_participant_status")
    _rename_constraint(schema, "session_participants",
                       "pk__dining_sessions", "pk__session_participants")
    _rename_constraint(schema, "session_participants",
                       "fk__dining_sessions__dining_table_id__dining_tables",
                       "fk__session_participants__dining_table_id__dining_tables")
    _rename_index(schema, "ix_tenant_dining_sessions_dining_table_id",
                  "ix_tenant_session_participants_dining_table_id")

    # display_label arranca igual al nombre; la desambiguación aplica de aquí
    # en adelante (no se puede reconstruir el orden de llegada retroactivo).
    op.execute(text(
        f'UPDATE "{schema}".session_participants SET display_label = display_name'
    ))

    # --- 3. backfill de table_sessions ------------------------------------
    # Comensales abiertos: los que comparten mesa comparten sesión (es la
    # situación real en curso). Cerrados: una sesión por comensal, porque el
    # agrupamiento histórico no está registrado en ninguna parte.
    op.execute(text(f'''
        INSERT INTO "{schema}".table_sessions (dining_table_id, status, opened_at)
        SELECT dining_table_id, 'active', MIN(joined_at)
        FROM "{schema}".session_participants
        WHERE status = 'open'
        GROUP BY dining_table_id
    '''))
    op.execute(text(f'''
        UPDATE "{schema}".session_participants p
        SET table_session_id = ts.id
        FROM "{schema}".table_sessions ts
        WHERE p.status = 'open'
          AND ts.status = 'active'
          AND ts.dining_table_id = p.dining_table_id
    '''))

    op.execute(text(f'''
        WITH nuevos AS (
            INSERT INTO "{schema}".table_sessions
                (dining_table_id, status, opened_at, closed_at)
            SELECT dining_table_id, 'closed', joined_at,
                   COALESCE(closed_at, joined_at)
            FROM "{schema}".session_participants
            WHERE table_session_id IS NULL
            RETURNING id, dining_table_id, opened_at
        )
        UPDATE "{schema}".session_participants p
        SET table_session_id = n.id
        FROM nuevos n
        WHERE p.table_session_id IS NULL
          AND p.dining_table_id = n.dining_table_id
          AND p.joined_at = n.opened_at
    '''))

    # Red de seguridad: si algún participante quedó sin sesión (empate exacto de
    # joined_at en la misma mesa), se le crea una propia antes del NOT NULL.
    huerfanos = bind.execute(text(
        f'SELECT id, dining_table_id, joined_at, closed_at '
        f'FROM "{schema}".session_participants WHERE table_session_id IS NULL'
    )).fetchall()
    for pid, table_id, joined_at, closed_at in huerfanos:
        ts_id = bind.execute(text(f'''
            INSERT INTO "{schema}".table_sessions
                (dining_table_id, status, opened_at, closed_at)
            VALUES (:tid, 'closed', :opened, :closed)
            RETURNING id
        '''), {"tid": table_id, "opened": joined_at,
               "closed": closed_at or joined_at}).scalar()
        bind.execute(text(
            f'UPDATE "{schema}".session_participants '
            f'SET table_session_id = :ts WHERE id = :pid'
        ), {"ts": ts_id, "pid": pid})

    op.alter_column("session_participants", "table_session_id",
                    nullable=False, schema=schema)
    op.create_foreign_key(
        "fk__session_participants__table_session_id__table_sessions",
        "session_participants", "table_sessions", ["table_session_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )
    op.create_index(
        "ix_tenant_session_participants_table_session_id", "session_participants",
        ["table_session_id"], schema=schema,
    )

    # --- 4. renombre de las FK que apuntaban a la sesión del comensal ------
    op.alter_column("carts", "session_id", new_column_name="participant_id", schema=schema)
    op.alter_column("order_items", "session_id", new_column_name="participant_id", schema=schema)
    op.alter_column("customer_orders", "dining_session_id",
                    new_column_name="participant_id", schema=schema)
    op.alter_column("sales", "dining_session_id",
                    new_column_name="participant_id", schema=schema)

    for tabla, old_fk in (
        ("carts", "fk__carts__session_id__dining_sessions"),
        ("order_items", "fk__order_items__session_id__dining_sessions"),
        ("customer_orders", "fk__customer_orders__dining_session_id__dining_sessions"),
        ("sales", "fk__sales__dining_session_id__dining_sessions"),
    ):
        _rename_constraint(schema, tabla, old_fk,
                           f"fk__{tabla}__participant_id__session_participants")

    _rename_index(schema, "ix_tenant_carts_session_id",
                  "ix_tenant_carts_participant_id")
    _rename_index(schema, "ix_tenant_order_items_session_id",
                  "ix_tenant_order_items_participant_id")
    _rename_index(schema, "ix_tenant_customer_orders_dining_session_id",
                  "ix_tenant_customer_orders_participant_id")

    op.drop_index("idx_open_cart_per_session", table_name="carts", schema=schema)
    op.create_index(
        "idx_open_cart_per_participant", "carts", ["participant_id"], unique=True,
        postgresql_where=sa.text("status = 'abierto'"), schema=schema,
    )

    # --- 5. table_session_id en pedidos y ventas --------------------------
    for tabla in ("customer_orders", "sales"):
        op.add_column(tabla, sa.Column("table_session_id", sa.UUID(as_uuid=True),
                                       nullable=True), schema=schema)
        op.create_foreign_key(
            f"fk__{tabla}__table_session_id__table_sessions", tabla, "table_sessions",
            ["table_session_id"], ["id"],
            source_schema=schema, referent_schema=schema,
        )
        op.create_index(f"ix_tenant_{tabla}_table_session_id", tabla,
                        ["table_session_id"], schema=schema)
        op.execute(text(f'''
            UPDATE "{schema}".{tabla} t
            SET table_session_id = p.table_session_id
            FROM "{schema}".session_participants p
            WHERE t.participant_id = p.id
        '''))

    # --- 6. una mesa puede tener varios pedidos a la vez ------------------
    op.drop_index("idx_open_order_per_table", table_name="customer_orders", schema=schema)

    # --- 7. estado 'recibida' --------------------------------------------
    op.drop_constraint("ck_customer_order_status",
                       "customer_orders", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_customer_order_status", "customer_orders",
        "status IN ('recibida', 'abierta', 'bloqueada', 'pagada', 'cancelada')",
        schema=schema,
    )

    # --- 8. cancelación por el comensal -----------------------------------
    op.alter_column("order_cancel_logs", "user_id", nullable=True, schema=schema)
    op.add_column("order_cancel_logs",
                  sa.Column("participant_id", sa.UUID(as_uuid=True), nullable=True),
                  schema=schema)
    op.create_foreign_key(
        "fk__order_cancel_logs__participant_id__session_participants",
        "order_cancel_logs", "session_participants", ["participant_id"], ["id"],
        source_schema=schema, referent_schema=schema,
    )


@for_each_tenant_schema
def downgrade(schema: str) -> None:
    if not _has_table(schema, "session_participants"):
        return

    op.drop_constraint("fk__order_cancel_logs__participant_id__session_participants",
                       "order_cancel_logs", type_="foreignkey", schema=schema)
    op.drop_column("order_cancel_logs", "participant_id", schema=schema)
    # Los logs de cancelación del comensal no tienen user_id: sin él no se puede
    # volver a NOT NULL, así que se descartan.
    op.execute(text(f'DELETE FROM "{schema}".order_cancel_logs WHERE user_id IS NULL'))
    op.alter_column("order_cancel_logs", "user_id", nullable=False, schema=schema)

    op.execute(text(
        f"UPDATE \"{schema}\".customer_orders SET status = 'abierta' "
        f"WHERE status = 'recibida'"
    ))
    op.drop_constraint("ck_customer_order_status",
                       "customer_orders", type_="check", schema=schema)
    op.create_check_constraint(
        "ck_customer_order_status", "customer_orders",
        "status IN ('abierta', 'bloqueada', 'pagada', 'cancelada')",
        schema=schema,
    )
    op.create_index(
        "idx_open_order_per_table", "customer_orders", ["dining_table_id"],
        unique=True, postgresql_where=sa.text("status = 'abierta'"), schema=schema,
    )

    for tabla in ("customer_orders", "sales"):
        op.drop_index(f"ix_tenant_{tabla}_table_session_id", table_name=tabla, schema=schema)
        op.drop_constraint(f"fk__{tabla}__table_session_id__table_sessions", tabla,
                           type_="foreignkey", schema=schema)
        op.drop_column(tabla, "table_session_id", schema=schema)

    # El índice parcial se recrea después de renombrar la columna, no antes.
    op.drop_index("idx_open_cart_per_participant", table_name="carts", schema=schema)

    for tabla in ("carts", "order_items", "customer_orders", "sales"):
        _rename_constraint(schema, tabla,
                           f"fk__{tabla}__participant_id__session_participants",
                           f"fk__{tabla}__session_id__dining_sessions"
                           if tabla in ("carts", "order_items")
                           else f"fk__{tabla}__dining_session_id__dining_sessions")
    _rename_index(schema, "ix_tenant_carts_participant_id", "ix_tenant_carts_session_id")
    _rename_index(schema, "ix_tenant_order_items_participant_id",
                  "ix_tenant_order_items_session_id")
    _rename_index(schema, "ix_tenant_customer_orders_participant_id",
                  "ix_tenant_customer_orders_dining_session_id")

    op.alter_column("carts", "participant_id", new_column_name="session_id", schema=schema)
    op.alter_column("order_items", "participant_id", new_column_name="session_id", schema=schema)
    op.alter_column("customer_orders", "participant_id",
                    new_column_name="dining_session_id", schema=schema)
    op.alter_column("sales", "participant_id",
                    new_column_name="dining_session_id", schema=schema)
    op.create_index(
        "idx_open_cart_per_session", "carts", ["session_id"], unique=True,
        postgresql_where=sa.text("status = 'abierto'"), schema=schema,
    )

    op.drop_index("ix_tenant_session_participants_table_session_id",
                  table_name="session_participants", schema=schema)
    op.drop_constraint("fk__session_participants__table_session_id__table_sessions",
                       "session_participants", type_="foreignkey", schema=schema)
    op.drop_column("session_participants", "table_session_id", schema=schema)
    op.drop_column("session_participants", "display_label", schema=schema)
    _rename_constraint(schema, "session_participants",
                       "ck__session_participants__ck_session_participant_status",
                       "ck__dining_sessions__ck_dining_session_status")
    _rename_constraint(schema, "session_participants",
                       "pk__session_participants", "pk__dining_sessions")
    _rename_constraint(schema, "session_participants",
                       "fk__session_participants__dining_table_id__dining_tables",
                       "fk__dining_sessions__dining_table_id__dining_tables")
    _rename_index(schema, "ix_tenant_session_participants_dining_table_id",
                  "ix_tenant_dining_sessions_dining_table_id")
    op.alter_column("session_participants", "joined_at",
                    new_column_name="opened_at", schema=schema)
    op.alter_column("session_participants", "display_name",
                    new_column_name="customer_name", schema=schema)
    op.rename_table("session_participants", "dining_sessions", schema=schema)

    op.drop_index("idx_active_session_per_table",
                  table_name="table_sessions", schema=schema)
    op.drop_index("ix_tenant_table_sessions_dining_table_id",
                  table_name="table_sessions", schema=schema)
    op.drop_table("table_sessions", schema=schema)
