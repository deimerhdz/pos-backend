"""Test de la sesión de mesa: unión, desambiguación de nombres y reingreso.

No hay pytest en el proyecto, así que es un script autoejecutable:

    python -m app.scripts.test_table_sessions

Cubre las reglas que hacen que el QR sea usable y seguro:
  - escanear el QR de una mesa ocupada **une** a la sesión en curso, no abre otra;
  - dos comensales con el mismo nombre se distinguen (`display_label`);
  - el identificador real es el id firmado, no el nombre;
  - reingreso: token de una sesión de mesa ya cerrada → 401 (nunca se reutiliza un
    `table_session_id` inválido);
  - token emitido para otra mesa → 401, se fuerza el flujo de comensal nuevo.

Crea su propia mesa desechable y la borra al terminar.
"""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from app.core.db import with_db
from app.core.qr_context import open_session_context
from app.core.qr_token import mint_session_token
from app.models.cart import Cart
from app.models.dining_table import DiningTable
from app.models.session_participant import SessionParticipant
from app.models.table_session import TableSession
from app.api.v1.cart import service as cart_service


def _tenant() -> tuple[int, str]:
    with with_db(None) as db:
        row = db.execute(
            text("SELECT id, schema FROM shared.tenants ORDER BY id LIMIT 1")
        ).first()
    if row is None:
        raise SystemExit("No hay tenants en shared.tenants.")
    return row[0], row[1]


def _check(label, actual, esperado):
    if actual != esperado:
        raise AssertionError(f"{label}: esperado {esperado}, obtenido {actual}")
    print(f"  ok  · {label}")


def _expect_401(fn, label):
    try:
        fn()
    except HTTPException as e:
        if e.status_code != 401:
            raise AssertionError(f"{label}: esperado 401, vino {e.status_code}")
        print(f"  ok  · {label}")
        return
    raise AssertionError(f"{label}: no lanzó 401")


def _cleanup(schema: str, table_id) -> None:
    with with_db(schema) as db:
        db.execute(text(f'''
            DELETE FROM "{schema}".carts WHERE participant_id IN (
                SELECT id FROM "{schema}".session_participants WHERE dining_table_id = :t)
        '''), {"t": str(table_id)})
        db.execute(text(f'DELETE FROM "{schema}".session_participants '
                        f'WHERE dining_table_id = :t'), {"t": str(table_id)})
        db.execute(text(f'DELETE FROM "{schema}".table_sessions '
                        f'WHERE dining_table_id = :t'), {"t": str(table_id)})
        db.execute(text(f'DELETE FROM "{schema}".dining_tables WHERE id = :t'),
                   {"t": str(table_id)})
        db.commit()


def main():
    tenant_id, schema = _tenant()
    print(f"Sesión de mesa y reingreso (tenant: {schema})")

    with with_db(schema) as db:
        numero = 9000 + int(uuid4().int % 900)
        table = DiningTable(number=numero, name=f"mesa-test-{uuid4().hex[:6]}")
        db.add(table)
        db.commit()
        db.refresh(table)
        table_id = table.id

    try:
        with with_db(schema) as db:
            table = db.get(DiningTable, table_id)

            # --- 1. primer comensal abre la sesión de mesa -------------------
            r1 = cart_service.open_session(db, tenant_id, table, "Ana")
            _check("el primer escaneo abre la sesión", r1.display_label, "Ana")

            # --- 2. segundo comensal con el MISMO nombre ---------------------
            table = db.get(DiningTable, table_id)
            r2 = cart_service.open_session(db, tenant_id, table, "Ana")
            _check("el segundo escaneo se une a la MISMA sesión de mesa",
                   r2.table_session_id, r1.table_session_id)
            _check("nombre duplicado se desambigua", r2.display_label, "Ana (2)")
            _check("pero el display_name se conserva tal cual", r2.display_name, "Ana")
            if r1.participant_id == r2.participant_id:
                raise AssertionError("dos comensales distintos comparten participant_id")
            print("  ok  · el identificador real es el participant_id, no el nombre")

            n = db.execute(
                select(TableSession).where(TableSession.dining_table_id == table_id)
            ).scalars().all()
            _check("la mesa tiene una sola sesión", len(n), 1)

            carts = db.execute(
                select(Cart).where(Cart.participant_id.in_(
                    [r1.participant_id, r2.participant_id]))
            ).scalars().all()
            _check("cada comensal tiene su propio carrito borrador", len(carts), 2)

        # --- 3. reingreso con token válido -------------------------------
        with open_session_context(r1.session_token) as ctx:
            _check("reingreso con token válido restaura al comensal",
                   ctx.participant.id, r1.participant_id)

        # --- 4. token de una sesión de mesa ya cerrada -------------------
        with with_db(schema) as db:
            ts = db.get(TableSession, r1.table_session_id)
            ts.status = "closed"
            db.commit()

        _expect_401(
            lambda: open_session_context(r1.session_token).__enter__(),
            "token de una sesión de mesa cerrada → 401",
        )

        # --- 5. token emitido para otra mesa -----------------------------
        ajeno = mint_session_token(tenant_id, uuid4(), r1.participant_id, uuid4())
        _expect_401(
            lambda: open_session_context(ajeno).__enter__(),
            "token con otra mesa/sesión → 401 (fuerza comensal nuevo)",
        )

        print("TODO OK ✔")
    finally:
        _cleanup(schema, table_id)


if __name__ == "__main__":
    main()
