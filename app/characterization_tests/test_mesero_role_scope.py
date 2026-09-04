"""Bloqueo de alcance del rol Mesero (spec 075, User Story 3, FR-007).

Verifica, directamente sobre `get_current_user()`, que un usuario con rol
MESERO solo puede alcanzar las rutas listadas en `_MESERO_ALLOWED_ROUTES`
(default-deny fuera de esa lista — research.md D2/D3), y que Admin y Cajero
no pierden ni ganan ningún acceso por la verificación nueva (FR-008/SC-004).
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import auth_fixtures as af
from app.core.dependencies import _API_PREFIX, get_current_user
from app.core.utils import create_access_token, decode_token


class _FakeRoute:
    """Doble mínimo de `starlette.routing.Route`: solo `path_format`, que es
    lo único que `_enforce_mesero_scope()` lee de `request.scope["route"]`."""

    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


class _FakeState:
    pass


class _FakeRequest:
    """Doble mínimo de `fastapi.Request` — solo lo que `get_current_user()` y
    `_enforce_mesero_scope()` leen/escriben: `.method`, `.scope["route"]` y
    `.state` (para el efecto colateral de logging, spec 074)."""

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.scope = {"route": _FakeRoute(_API_PREFIX + path)}
        self.state = _FakeState()


class MeseroRoleScopeTests(unittest.TestCase):
    def setUp(self):
        self.db = af.new_session()
        self.tenant = af.make_tenant(self.db)

    def _user_with_role(self, role_name: str):
        role = af.make_role(self.db, name=role_name)
        user = af.make_user(self.db, tenant=self.tenant, role=role)
        self.db.commit()
        return user

    def _call(self, user, method: str, path: str):
        user_data = {
            "email": user.email,
            "uid": str(user.id),
            "tenant_id": self.tenant.id,
            "is_super_admin": False,
            "role": user.role_name,
            "must_change_password": user.must_change_password,
        }
        claims = decode_token(create_access_token(user_data))
        req = _FakeRequest(method, path)
        return get_current_user(token_data=claims, db=self.db, tenant=self.tenant, req=req)

    # -- Mesero: dentro de su alcance (permitido) ----------------------------

    def test_mesero_puede_listar_sesiones_de_mesa(self):
        user = self._user_with_role("MESERO")
        result = self._call(user, "GET", "/table-sessions")
        self.assertEqual(result.id, user.id)

    def test_mesero_puede_cobrar_una_orden(self):
        user = self._user_with_role("MESERO")
        result = self._call(user, "POST", "/orders/{order_id}/pay")
        self.assertEqual(result.id, user.id)

    def test_mesero_puede_consultar_ordenes(self):
        user = self._user_with_role("MESERO")
        result = self._call(user, "GET", "/orders")
        self.assertEqual(result.id, user.id)

    def test_mesero_puede_ver_metodos_de_pago_para_cobrar(self):
        user = self._user_with_role("MESERO")
        result = self._call(user, "GET", "/sales/payment-methods")
        self.assertEqual(result.id, user.id)

    # -- Mesero: fuera de su alcance (bloqueado) -----------------------------

    def test_mesero_no_puede_ver_inventario(self):
        user = self._user_with_role("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            self._call(user, "GET", "/inventario")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mesero_no_puede_crear_venta_de_mostrador(self):
        user = self._user_with_role("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            self._call(user, "POST", "/sales")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mesero_no_puede_abrir_turno_de_caja(self):
        user = self._user_with_role("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            self._call(user, "POST", "/cash/shifts/open")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mesero_no_puede_listar_usuarios(self):
        user = self._user_with_role("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            self._call(user, "GET", "/users")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mesero_no_puede_crear_mesa(self):
        """`POST /orders/tables` ya exige `require_tenant_admin` (bloqueado sin
        necesidad de allow-list), pero la verificación nueva debe coincidir con
        ese resultado en vez de contradecirlo."""
        user = self._user_with_role("MESERO")
        with self.assertRaises(HTTPException) as ctx:
            self._call(user, "POST", "/orders/tables")
        self.assertEqual(ctx.exception.status_code, 403)

    # -- Admin y Cajero: sin cambios (FR-008/SC-004) -------------------------

    def test_admin_no_se_ve_afectado_por_la_verificacion_nueva(self):
        user = self._user_with_role("ADMIN")
        result = self._call(user, "GET", "/inventario")
        self.assertEqual(result.id, user.id)

    def test_cajero_no_se_ve_afectado_al_crear_una_venta(self):
        user = self._user_with_role("CASHIER")
        result = self._call(user, "POST", "/sales")
        self.assertEqual(result.id, user.id)

    def test_cajero_no_se_ve_afectado_al_abrir_turno_de_caja(self):
        user = self._user_with_role("CASHIER")
        result = self._call(user, "POST", "/cash/shifts/open")
        self.assertEqual(result.id, user.id)


if __name__ == "__main__":
    unittest.main()
