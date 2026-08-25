"""POST /invitations (spec 037, US1).

Tests de la nueva funcionalidad — no son characterization tests: no existía
ningún endpoint de invitaciones antes de esta spec. Ejercita
`create_invitation` directamente (sin `TestClient`, research.md sección
Testing) usando `auth_fixtures` (SQLite en memoria).

    python -m unittest app.characterization_tests.test_invitations_create -v
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select

from app.characterization_tests import auth_fixtures as af
from app.api.v1.invitations.router import create_invitation
from app.api.v1.invitations.schemas import InvitationCreate, InvitationResponse
from app.core.dependencies import require_tenant_admin
from app.core.models import User, UserInvitation
from app.models.plan import Plan


def _make_admin(db, tenant):
    role = af.make_role(db, name="ADMIN")
    return af.make_user(db, tenant=tenant, role=role, password="claveActual1")


def _count(db, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    if where:
        stmt = stmt.where(*where)
    return db.execute(stmt).scalar_one()


class CreateInvitationTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db)
        self.admin = _make_admin(self.db, self.tenant)
        self.cashier_role = af.make_role(self.db, name="CASHIER")
        self.db.commit()

    def _create(self, email="cajero1@acme.com", role="CASHIER", tenant=None, admin=None):
        with patch("app.api.v1.invitations.router.send_email") as mock_send:
            resp = create_invitation(
                InvitationCreate(email=email, role=role),
                tenant=tenant or self.tenant,
                admin=admin or self.admin,
                db=self.db,
            )
        return resp, mock_send

    # ---------------------------------------------------------- Acceptance Scenarios 2-3

    def test_creacion_exitosa_sin_user_nuevo_ni_contrasena_en_la_respuesta(self):
        resp, mock_send = self._create()

        self.assertEqual(resp.email, "cajero1@acme.com")
        self.assertEqual(resp.role_name, "CASHIER")

        invitation = self.db.execute(select(UserInvitation)).scalar_one()
        self.assertEqual(invitation.status, "pending")
        self.assertEqual(invitation.email, "cajero1@acme.com")

        # Solo self.admin, ningún `User` nuevo (Acceptance Scenario 2).
        self.assertEqual(_count(self.db, User), 1)

        mock_send.assert_called_once()
        serialized = str(InvitationResponse.model_validate(resp).model_dump())
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn(invitation.password_hash, serialized)

    def test_correo_se_normaliza_antes_de_comparar_y_guardar(self):
        resp, _ = self._create(email="  Cajero1@ACME.com  ")
        self.assertEqual(resp.email, "cajero1@acme.com")

    # ---------------------------------------------------------- Edge case FR-015 (carrera)

    def test_invitacion_pendiente_duplicada_es_409_solo_una_fila_sobrevive(self):
        self._create(email="dup@acme.com")

        with patch("app.api.v1.invitations.router.send_email") as mock_send:
            with self.assertRaises(HTTPException) as ctx:
                create_invitation(
                    InvitationCreate(email="dup@acme.com", role="CASHIER"),
                    tenant=self.tenant, admin=self.admin, db=self.db,
                )
        mock_send.assert_not_called()  # SC-006: nunca se envía correo para un duplicado

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("invitación pendiente", ctx.exception.detail)
        self.assertEqual(_count(self.db, UserInvitation, UserInvitation.email == "dup@acme.com"), 1)

    # ---------------------------------------------------------- FR-014 (unicidad por tenant, no global)

    def test_mismo_correo_en_dos_tenants_distintos_no_colisiona(self):
        # Role es una tabla global (no por tenant) — se reutiliza el mismo
        # `self.cashier_role` en vez de crear un segundo "CASHIER" duplicado.
        other_tenant = af.make_tenant(self.db)
        other_admin = _make_admin(self.db, other_tenant)
        self.db.commit()

        resp1, _ = self._create(email="compartido@acme.com", tenant=self.tenant, admin=self.admin)
        resp2, _ = self._create(email="compartido@acme.com", tenant=other_tenant, admin=other_admin)

        self.assertNotEqual(resp1.id, resp2.id)

    # ---------------------------------------------------------- Acceptance Scenario 5 + Clarification 1

    def test_correo_de_user_activo_es_409(self):
        af.make_user(self.db, tenant=self.tenant, role=self.cashier_role, email="ya-existe@acme.com")
        self.db.commit()

        with patch("app.api.v1.invitations.router.send_email") as mock_send:
            with self.assertRaises(HTTPException) as ctx:
                create_invitation(
                    InvitationCreate(email="ya-existe@acme.com", role="CASHIER"),
                    tenant=self.tenant, admin=self.admin, db=self.db,
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("usuario", ctx.exception.detail)
        mock_send.assert_not_called()  # SC-006

    def test_correo_de_user_inactivo_tambien_es_409_mismo_mensaje(self):
        af.make_user(
            self.db, tenant=self.tenant, role=self.cashier_role,
            email="baja@acme.com", active=False,
        )
        self.db.commit()

        with patch("app.api.v1.invitations.router.send_email") as mock_send:
            with self.assertRaises(HTTPException) as ctx:
                create_invitation(
                    InvitationCreate(email="baja@acme.com", role="CASHIER"),
                    tenant=self.tenant, admin=self.admin, db=self.db,
                )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("usuario", ctx.exception.detail)
        mock_send.assert_not_called()  # SC-006

    # ---------------------------------------------------------- Acceptance Scenario 4

    def test_cashier_no_puede_invitar(self):
        cashier = af.make_user(self.db, tenant=self.tenant, role=self.cashier_role)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            require_tenant_admin(user=cashier)
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------------------------------------------------------- FR-018 (plan vencido)

    def test_tenant_con_plan_vencido_es_403_sin_enviar_correo(self):
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        expired_tenant = af.make_tenant(self.db, plan_vence_en=past)
        admin = _make_admin(self.db, expired_tenant)
        self.db.commit()

        with patch("app.api.v1.invitations.router.send_email") as mock_send:
            with self.assertRaises(HTTPException) as ctx:
                create_invitation(
                    InvitationCreate(email="nuevo@acme.com", role="CASHIER"),
                    tenant=expired_tenant,
                    admin=admin,
                    db=self.db,
                )
        self.assertEqual(ctx.exception.status_code, 403)
        mock_send.assert_not_called()

    # ---------------------------------------------------------- research.md Decisión 5

    def test_limite_de_plan_cuenta_invitaciones_pendientes_sin_ningun_user_real(self):
        # Cupo de 2: 1 ya lo ocupa el propio ADMIN (un `User` real); el
        # cupo restante lo agota la primera invitación *pendiente* (sin
        # ningún otro `User` real) — distingue el conteo extendido de T007.
        plan = Plan(name="plan-limitado", usuarios_limit=2)
        self.db.add(plan)
        self.db.flush()
        limited_tenant = af.make_tenant(self.db, plan_id=plan.id)
        admin = _make_admin(self.db, limited_tenant)
        self.db.commit()

        self._create(email="primero@acme.com", tenant=limited_tenant, admin=admin)

        with self.assertRaises(HTTPException) as ctx:
            self._create(email="segundo@acme.com", tenant=limited_tenant, admin=admin)
        self.assertEqual(ctx.exception.status_code, 403)

    # ---------------------------------------------------------- FR-012

    def test_fallo_de_envio_no_deja_invitacion_persistida(self):
        with patch(
            "app.api.v1.invitations.router.send_email", side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                create_invitation(
                    InvitationCreate(email="fallo@acme.com", role="CASHIER"),
                    tenant=self.tenant,
                    admin=self.admin,
                    db=self.db,
                )
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(_count(self.db, UserInvitation, UserInvitation.email == "fallo@acme.com"), 0)


if __name__ == "__main__":
    unittest.main()
