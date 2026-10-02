"""POST /auth/login — aislamiento plataforma / negocio (spec 091, A-99).

Una prueba por fila de la tabla de decisión de
`specs/091-admin-subdominio-nombre-completo/contracts/auth-login-platform-isolation.md`.
Mismo patrón que `test_auth_login_invitation_consumption.py`: `login()` abre su
propia sesión vía `with_db`, que se parchea con la sesión SQLite de `auth_fixtures`.

    python -m unittest app.characterization_tests.test_auth_login_platform_isolation -v
"""
import asyncio
import json
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.auth import routes as auth_routes
from app.api.v1.auth.schemas import LoginRequest
from app.characterization_tests import auth_fixtures as af
from app.core import dependencies as deps
from app.core.models import User, UserInvitation

SA_EMAIL = "root@platform.com"
SA_PASSWORD = "Platform123!"
UN_EMAIL = "cajero@acme.com"
UN_PASSWORD = "Negocio123!"


class _FakeRequest:
    def __init__(self, tenant_host: str | None):
        self.headers = {"x-tenant-host": tenant_host} if tenant_host is not None else {}


class LoginPlatformIsolationTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db, host="acme", name="Acme")
        self.role_sa = af.make_role(self.db, name="SUPER_ADMIN")
        self.role = af.make_role(self.db, name="ADMIN")
        self.sa = af.make_user(
            self.db, None, self.role_sa, password=SA_PASSWORD, email=SA_EMAIL
        )
        self.un = af.make_user(
            self.db, self.tenant, self.role, password=UN_PASSWORD, email=UN_EMAIL
        )
        self.db.commit()

        @contextmanager
        def fake_with_db(schema):
            yield self.db

        patcher = patch("app.api.v1.auth.routes.with_db", fake_with_db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _login(self, email, password, host):
        return asyncio.run(
            auth_routes.login(
                LoginRequest(email=email, password=password), _FakeRequest(host)
            )
        )

    def _rejected(self, email, password, host) -> HTTPException:
        with self.assertRaises(HTTPException) as ctx:
            self._login(email, password, host)
        return ctx.exception

    def _assert_invalid_credentials(self, exc: HTTPException):
        self.assertEqual(exc.status_code, 401)
        self.assertEqual(exc.detail, "Invalid credentials")

    # ------------------------------------------------------------- filas 1 y 2

    def test_fila_1_super_admin_activo_en_admin_entra(self):
        resp = self._login(SA_EMAIL, SA_PASSWORD, "admin")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.body)
        self.assertTrue(body["user"]["is_super_admin"])

    def test_variantes_de_admin_cuentan_como_plataforma(self):
        for host in ("ADMIN", " admin ", "admin:4200"):
            with self.subTest(host=host):
                resp = self._login(SA_EMAIL, SA_PASSWORD, host)
                self.assertEqual(resp.status_code, 200)

    def test_fila_2_super_admin_inactivo_en_admin_403(self):
        self.sa.active = False
        self.db.commit()
        exc = self._rejected(SA_EMAIL, SA_PASSWORD, "admin")
        self.assertEqual(exc.status_code, 403)
        self.assertEqual(exc.detail, "User account is inactive")

    # ------------------------------------------------------------- filas 3 a 8

    def test_fila_3_usuario_de_negocio_en_admin_401(self):
        self._assert_invalid_credentials(self._rejected(UN_EMAIL, UN_PASSWORD, "admin"))

    def test_fila_4_usuario_de_negocio_en_su_subdominio_entra(self):
        resp = self._login(UN_EMAIL, UN_PASSWORD, "acme")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(json.loads(resp.body)["user"]["is_super_admin"])

    def test_fila_5_super_admin_en_subdominio_de_negocio_401(self):
        self._assert_invalid_credentials(self._rejected(SA_EMAIL, SA_PASSWORD, "acme"))

    def test_fila_6_super_admin_en_host_inexistente_401(self):
        self._assert_invalid_credentials(self._rejected(SA_EMAIL, SA_PASSWORD, "no-existe"))

    def test_fila_7_usuario_de_negocio_en_host_inexistente_401(self):
        self._assert_invalid_credentials(self._rejected(UN_EMAIL, UN_PASSWORD, "no-existe"))

    def test_fila_8_cabecera_ausente_o_vacia_401(self):
        for email, password in ((SA_EMAIL, SA_PASSWORD), (UN_EMAIL, UN_PASSWORD)):
            for host in (None, ""):
                with self.subTest(email=email, host=host):
                    self._assert_invalid_credentials(self._rejected(email, password, host))

    def test_fila_9_credenciales_incorrectas_siguen_en_401(self):
        self._assert_invalid_credentials(self._rejected(SA_EMAIL, "otra-clave", "admin"))
        self._assert_invalid_credentials(self._rejected(UN_EMAIL, "otra-clave", "acme"))

    # ------------------------------------------------------------ filas 10 y 11

    def test_fila_10_invitacion_pendiente_en_el_negocio_se_consume(self):
        af.make_invitation(
            self.db, self.tenant, role=self.role, password="Temporal123!",
            email="invitado@acme.com",
        )
        self.db.commit()
        resp = self._login("invitado@acme.com", "Temporal123!", "acme")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.db.execute(
                select(func.count()).select_from(User).where(User.email == "invitado@acme.com")
            ).scalar_one(),
            1,
        )

    def test_fila_11_invitacion_pendiente_no_se_consume_en_admin(self):
        inv = af.make_invitation(
            self.db, self.tenant, role=self.role, password="Temporal123!",
            email="invitado@acme.com",
        )
        self.db.commit()
        self._assert_invalid_credentials(
            self._rejected("invitado@acme.com", "Temporal123!", "admin")
        )
        self.db.refresh(inv)
        self.assertEqual(inv.status, "pending")

    # --------------------------------------------------------------- invariantes

    def test_i1_los_rechazos_son_indistinguibles(self):
        rejected = [
            self._rejected(UN_EMAIL, UN_PASSWORD, "admin"),
            self._rejected(SA_EMAIL, SA_PASSWORD, "acme"),
            self._rejected(SA_EMAIL, SA_PASSWORD, "no-existe"),
            self._rejected(UN_EMAIL, UN_PASSWORD, "no-existe"),
            self._rejected(SA_EMAIL, SA_PASSWORD, None),
            self._rejected(SA_EMAIL, "otra-clave", "admin"),
        ]
        self.assertEqual({(e.status_code, e.detail) for e in rejected}, {(401, "Invalid credentials")})

    def test_i3_un_rechazo_no_crea_usuario_ni_consume_invitacion(self):
        inv = af.make_invitation(
            self.db, self.tenant, role=self.role, password="Temporal123!",
            email="invitado@acme.com",
        )
        self.db.commit()
        users_before = self.db.execute(select(func.count()).select_from(User)).scalar_one()
        for host in ("admin", "no-existe", None):
            self._rejected("invitado@acme.com", "Temporal123!", host)
        self.assertEqual(
            self.db.execute(select(func.count()).select_from(User)).scalar_one(), users_before
        )
        self.db.refresh(inv)
        self.assertEqual(inv.status, "pending")
        self.assertEqual(
            self.db.execute(select(func.count()).select_from(UserInvitation)).scalar_one(), 1
        )

    def test_negocio_con_host_admin_prueba_no_se_confunde_con_la_plataforma(self):
        other = af.make_tenant(self.db, host="admin-prueba", name="Admin Prueba")
        af.make_user(self.db, other, self.role, password="Otro12345!", email="u@prueba.com")
        self.db.commit()
        self.assertEqual(self._login("u@prueba.com", "Otro12345!", "admin-prueba").status_code, 200)
        self._assert_invalid_credentials(self._rejected(SA_EMAIL, SA_PASSWORD, "admin-prueba"))

    def test_negocio_con_host_guardado_en_mayuscula_se_resuelve_por_igualdad_exacta(self):
        other = af.make_tenant(self.db, host="Acme2", name="Acme2")
        af.make_user(self.db, other, self.role, password="Otro12345!", email="u@acme2.com")
        self.db.commit()
        self.assertEqual(self._login("u@acme2.com", "Otro12345!", "Acme2").status_code, 200)

    # ------------------------------------------------- FR-007 / I-5 (sin cambios)

    def _token_data(self, user: User) -> dict:
        return {
            "user": {
                "email": user.email,
                "uid": str(user.id),
                "tenant_id": user.tenant_id,
                "is_super_admin": user.tenant_id is None,
            }
        }

    def test_token_de_super_admin_no_pasa_get_current_user_de_un_negocio(self):
        with self.assertRaises(HTTPException) as ctx:
            deps.get_current_user(
                token_data=self._token_data(self.sa), db=self.db, tenant=self.tenant
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_token_de_usuario_de_negocio_no_pasa_get_current_super_admin(self):
        request = SimpleNamespace(state=SimpleNamespace())
        with self.assertRaises(HTTPException) as ctx:
            deps.get_current_super_admin(
                request=request, token_data=self._token_data(self.un), db=self.db
            )
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Super admin access required")


if __name__ == "__main__":
    unittest.main()
