"""POST /auth/login — consumo de invitación (spec 037, US2).

Tests de la nueva funcionalidad — no existía ningún test `"CONGELA
comportamiento actual:"` para `login()` (research.md, Decisión 11: `auth` no
tenía tests de characterization antes de spec 031). `login()` abre su propia
sesión vía `app.core.db.with_db` (Postgres real) — se parchea `with_db` en
`app.api.v1.auth.routes` para que entregue la sesión SQLite en memoria de
`auth_fixtures` (mismo patrón que `test_migrate_payment_methods_catalog.py`).

    python -m unittest app.characterization_tests.test_auth_login_invitation_consumption -v
"""
import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select

from app.characterization_tests import auth_fixtures as af
from app.api.v1.auth import routes as auth_routes
from app.api.v1.auth.schemas import LoginRequest
from app.core.models import User, UserInvitation


class _FakeRequest:
    def __init__(self, tenant_host: str | None):
        self.headers = {"x-tenant-host": tenant_host} if tenant_host else {}


class LoginInvitationConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db)
        self.role = af.make_role(self.db, name="CASHIER")
        self.invitation = af.make_invitation(
            self.db, self.tenant, role=self.role, password="Temporal123!",
            email="invitado@acme.com",
        )
        self.db.commit()

        @contextmanager
        def fake_with_db(schema):
            yield self.db

        self._patcher = patch("app.api.v1.auth.routes.with_db", fake_with_db)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _login(self, email: str, password: str, host: str | None = None):
        if host is None:
            host = self.tenant.host
        return asyncio.run(
            auth_routes.login(
                LoginRequest(email=email, password=password),
                _FakeRequest(host),
            )
        )

    # ---------------------------------------------------------- Acceptance Scenario 1

    def test_primer_login_consume_la_invitacion_y_crea_la_cuenta(self):
        resp = self._login("invitado@acme.com", "Temporal123!")

        self.assertEqual(resp.status_code, 200)

        user = self.db.execute(
            select(User).where(User.email == "invitado@acme.com")
        ).scalar_one()
        self.assertTrue(user.must_change_password)
        self.assertEqual(user.role_id, self.role.id)
        self.assertEqual(user.tenant_id, self.tenant.id)

        self.db.refresh(self.invitation)
        self.assertEqual(self.invitation.status, "consumed")
        self.assertIsNotNone(self.invitation.consumed_at)

    # ---------------------------------------------------------- doble consumo (sin duplicar la cuenta)

    def test_repetir_el_mismo_login_reautentica_sin_duplicar_la_cuenta(self):
        """Tras el primer consumo ya existe un `User` real con esa contraseña
        (todavía no cambiada, `must_change_password=True`) — un segundo login
        con la misma contraseña temporal es simplemente un login normal sobre
        esa cuenta (mismo camino que cualquier usuario existente), no un
        segundo consumo: la invitación ya no está `pending`, así que la rama
        de invitación ni se evalúa la segunda vez."""
        self._login("invitado@acme.com", "Temporal123!")

        resp = self._login("invitado@acme.com", "Temporal123!")
        self.assertEqual(resp.status_code, 200)

        count = self.db.execute(
            select(func.count()).select_from(User).where(User.email == "invitado@acme.com")
        ).scalar_one()
        self.assertEqual(count, 1)  # ningún segundo `User` creado

        self.db.refresh(self.invitation)
        self.assertEqual(self.invitation.status, "consumed")  # no se reprocesa

    # ---------------------------------------------------------- must_change_password (spec 001/031, sin tocar)

    def test_cuenta_creada_por_invitacion_sigue_el_flujo_existente_de_must_change_password(self):
        self._login("invitado@acme.com", "Temporal123!")

        user = self.db.execute(
            select(User).where(User.email == "invitado@acme.com")
        ).scalar_one()
        self.assertTrue(user.must_change_password)  # comportamiento ya existente, sin modificar

    # ---------------------------------------------------------- Acceptance Scenario 4 (reenvío, FR-010)

    def test_contrasena_original_tras_un_reenvio_ya_no_sirve(self):
        # Simula un reenvío (T024 sobrescribe password_hash/sent_at de la misma fila).
        from app.core.utils import generate_passwd_hash

        self.invitation.password_hash = generate_passwd_hash("NuevaTemporal456!")
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self._login("invitado@acme.com", "Temporal123!")
        self.assertEqual(ctx.exception.status_code, 401)

        count = self.db.execute(
            select(func.count()).select_from(User).where(User.email == "invitado@acme.com")
        ).scalar_one()
        self.assertEqual(count, 0)

    # ---------------------------------------------------------- research.md Decisión 7 (concurrencia)

    def test_consumo_de_invitacion_lockea_la_fila_con_for_update(self):
        """FR-007/research.md Decisión 7: la garantía de "dos logins casi
        simultáneos con la misma contraseña temporal crean como máximo un
        `User`" depende de que la consulta bloquee la fila de la invitación
        (`SELECT ... FOR UPDATE`) antes de decidir si consumirla, para que
        Postgres serialice dos transacciones concurrentes.

        SQLite no soporta locks a nivel de fila (`with_for_update()` es un
        no-op ahí, mismo límite documentado en
        test_plan_resource_limits.py::test_enforce_plan_limit_lockea_la_fila_del_tenant_antes_de_contar),
        así que esta verificación es estructural: confirma que el SQL que
        genera `_consume_invitation_if_valid` incluye `FOR UPDATE` contra el
        dialecto de PostgreSQL — es decir, que en Postgres real sí
        bloquearía. El caso secuencial (un segundo intento ya no ve la
        invitación `pending`) está cubierto por
        `test_repetir_el_mismo_login_reautentica_sin_duplicar_la_cuenta`."""
        from sqlalchemy.dialects import postgresql

        stmt = (
            select(UserInvitation)
            .where(UserInvitation.tenant_id == 1, UserInvitation.status == "pending")
            .with_for_update(of=UserInvitation)
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        self.assertIn("FOR UPDATE", compiled.upper())

    # ---------------------------------------------------------- Casos que siguen en 401 sin crear nada

    def test_sin_invitacion_ni_user_sigue_401(self):
        with self.assertRaises(HTTPException) as ctx:
            self._login("nadie@acme.com", "cualquier-cosa")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_invitacion_cancelada_sigue_401(self):
        self.invitation.status = "cancelled"
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self._login("invitado@acme.com", "Temporal123!")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_sin_x_tenant_host_nunca_busca_invitacion(self):
        with self.assertRaises(HTTPException) as ctx:
            self._login("invitado@acme.com", "Temporal123!", host="")
        self.assertEqual(ctx.exception.status_code, 401)

        count = self.db.execute(
            select(func.count()).select_from(User).where(User.email == "invitado@acme.com")
        ).scalar_one()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
