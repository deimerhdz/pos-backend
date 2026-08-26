"""GET /invitations (spec 037, US3).

Tests de la nueva funcionalidad. Ejercita `list_pending_invitations`
directamente (sin `TestClient`).

    python -m unittest app.characterization_tests.test_invitations_list -v
"""
import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.characterization_tests import auth_fixtures as af
from app.api.v1.auth import routes as auth_routes
from app.api.v1.auth.schemas import LoginRequest
from app.api.v1.invitations.router import list_pending_invitations
from app.api.v1.users.router import list_users


class _FakeRequest:
    def __init__(self, tenant_host):
        self.headers = {"x-tenant-host": tenant_host} if tenant_host else {}


class ListPendingInvitationsTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db)
        self.role = af.make_role(self.db, name="CASHIER")
        self.admin = af.make_user(self.db, tenant=self.tenant, role=self.role)

    def _login(self, email, password, host=None):
        @contextmanager
        def fake_with_db(schema):
            yield self.db

        with patch("app.api.v1.auth.routes.with_db", fake_with_db):
            return asyncio.run(
                auth_routes.login(
                    LoginRequest(email=email, password=password),
                    _FakeRequest(host or self.tenant.host),
                )
            )

    # ---------------------------------------------------------- Acceptance Scenario 1

    def test_lista_solo_las_pendientes_del_tenant_con_email_rol_y_fecha(self):
        af.make_user(self.db, tenant=self.tenant, role=self.role)
        inv1 = af.make_invitation(self.db, self.tenant, role=self.role, email="pend1@acme.com")
        inv2 = af.make_invitation(self.db, self.tenant, role=self.role, email="pend2@acme.com")
        self.db.commit()

        page = list_pending_invitations(page=1, size=20, db=self.db, admin=self.admin)

        self.assertEqual(page["total"], 2)
        emails = {item.email for item in page["items"]}
        self.assertEqual(emails, {"pend1@acme.com", "pend2@acme.com"})
        for item in page["items"]:
            self.assertEqual(item.role_name, "CASHIER")
            self.assertIsNotNone(item.sent_at)

        # GET /users sin cambios: solo las cuentas activas, ninguna invitación.
        users_page = list_users(page=1, size=20, db=self.db, admin=self.admin)
        self.assertEqual(users_page["total"], 2)  # self.admin + el User adicional

    # ---------------------------------------------------------- Acceptance Scenario 2

    def test_consumir_una_invitacion_la_saca_de_pendientes_y_la_pasa_a_activos(self):
        af.make_invitation(
            self.db, self.tenant, role=self.role, email="consume@acme.com", password="Temporal123!",
        )
        self.db.commit()

        self._login("consume@acme.com", "Temporal123!")

        page = list_pending_invitations(page=1, size=20, db=self.db, admin=self.admin)
        self.assertEqual(page["total"], 0)

        users_page = list_users(page=1, size=20, db=self.db, admin=self.admin)
        emails = {u.email for u in users_page["items"]}
        self.assertIn("consume@acme.com", emails)

    # ---------------------------------------------------------- FR-013

    def test_invitacion_de_otro_tenant_nunca_aparece(self):
        other_tenant = af.make_tenant(self.db)
        af.make_invitation(self.db, other_tenant, role=self.role, email="ajena@otro.com")
        self.db.commit()

        page = list_pending_invitations(page=1, size=20, db=self.db, admin=self.admin)
        self.assertEqual(page["total"], 0)


if __name__ == "__main__":
    unittest.main()
