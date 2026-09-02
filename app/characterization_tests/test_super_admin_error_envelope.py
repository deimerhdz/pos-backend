"""Envelope de error consistente del módulo super-admin (spec 068, Historias
de Usuario 1 y 3).

No son characterization tests: el envelope estructurado
(`success`/`error`/`request_id`/`detail`) es comportamiento enteramente
nuevo (Constitución, Principio IV/X), autorizado por `spec.md`. Usa
`starlette.testclient.TestClient` contra una app mínima
(`super_admin_http_fixtures.build_app`) — es la única forma de ejercitar
`RequestIdMiddleware`/`register_error_handlers` reales; los characterization
tests existentes de este módulo (`test_super_admin_plans.py`,
`test_super_admin_payment_catalog.py`) siguen invocando las funciones del
router directamente y por diseño no ven nada de esto (research.md § 5).

    python -m unittest app.characterization_tests.test_super_admin_error_envelope -v
"""
from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from app.characterization_tests import plan_fixtures as fx
from app.characterization_tests import super_admin_http_fixtures as hf

_UNSAFE_FRAGMENTS = ("Traceback", "psycopg", "SELECT ", "sqlalchemy", "File \"", "password", "secret")


class _EnvelopeAssertions:
    def assert_envelope(self, body: dict, *, expected_code: str | None = None):
        self.assertIn("success", body)
        self.assertFalse(body["success"])
        self.assertIn("error", body)
        self.assertIn("code", body["error"])
        self.assertIn("message", body["error"])
        self.assertIn("details", body["error"])
        self.assertIn("request_id", body)
        uuid.UUID(body["request_id"])  # no revienta si es un UUID válido
        # Campo de compatibilidad (spec.md § Clarifications / SC-006, T018):
        # `detail` de nivel superior debe coincidir con `error.message`.
        self.assertEqual(body.get("detail"), body["error"]["message"])
        if expected_code is not None:
            self.assertEqual(body["error"]["code"], expected_code)
        full_text = f"{body['error']['message']} {body['error'].get('details')}"
        for fragment in _UNSAFE_FRAGMENTS:
            self.assertNotIn(fragment, full_text, f"fuga de detalle técnico: {fragment!r} en {full_text!r}")


class SuperAdminErrorEnvelopeTests(unittest.TestCase, _EnvelopeAssertions):
    def setUp(self):
        self.db = hf.new_session()
        self.admin = hf.make_super_admin(self.db)
        self.db.commit()
        self.app = hf.build_app(self.db, token_data=hf.super_admin_token_data(self.admin))
        self.client = TestClient(self.app)

    # ---------------------------------------------------------- 404: tenant inexistente

    def test_tenant_inexistente_404(self):
        plan = fx.make_plan(self.db, precio_mensual=10000)
        self.db.commit()

        resp = self.client.patch(
            "/api/v1/super-admin/tenants/999999",
            json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
        )

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assert_envelope(body, expected_code="NOT_FOUND")
        self.assertEqual(body["detail"], "No existe ese tenant")

    # ---------------------------------------------------------- 404: plan inexistente

    def test_plan_inexistente_404(self):
        tenant = fx.make_tenant(self.db)
        self.db.commit()

        resp = self.client.patch(
            f"/api/v1/super-admin/tenants/{tenant.id}",
            json={"plan_id": str(uuid.uuid4()), "ciclo_facturacion": "mensual"},
        )

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assert_envelope(body, expected_code="NOT_FOUND")
        self.assertEqual(body["detail"], "No existe ese plan")

    # ---------------------------------------------------------- 409: ciclo sin precio

    def test_ciclo_facturacion_sin_precio_409(self):
        plan = fx.make_plan(self.db, name="Plan sin precio mensual")  # precio_mensual queda en None
        tenant = fx.make_tenant(self.db)
        self.db.commit()

        resp = self.client.patch(
            f"/api/v1/super-admin/tenants/{tenant.id}",
            json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
        )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assert_envelope(body, expected_code="CONFLICT")
        self.assertIn("Plan sin precio mensual", body["detail"])

    # ---------------------------------------------------------- 409: nombre de plan duplicado

    def test_nombre_de_plan_duplicado_409(self):
        fx.make_plan(self.db, name="Plan Pro")
        self.db.commit()

        resp = self.client.post("/api/v1/super-admin/plans", json={"name": "Plan Pro"})

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assert_envelope(body, expected_code="CONFLICT")

    # ---------------------------------------------------------- 403: sin rol de super-admin

    def test_sin_rol_de_super_admin_403(self):
        app = hf.build_app(self.db, token_data={"user": {"email": "cualquiera@example.com", "is_super_admin": False}})
        client = TestClient(app)

        resp = client.get("/api/v1/super-admin/tenants")

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assert_envelope(body, expected_code="FORBIDDEN")
        # No revela nada sobre tenants existentes (FR-004).
        self.assertNotIn("tenant", body["detail"].lower())

    # ---------------------------------------------------------- 401: super admin no encontrado

    def test_super_admin_no_encontrado_401(self):
        app = hf.build_app(
            self.db,
            token_data={"user": {"email": "no-existe@example.com", "is_super_admin": True}},
        )
        client = TestClient(app)

        resp = client.get("/api/v1/super-admin/tenants")

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assert_envelope(body, expected_code="UNAUTHORIZED")

    # ---------------------------------------------------------- 500: falla técnica inesperada

    def test_falla_tecnica_inesperada_500_sin_fuga_de_detalle(self):
        plan = fx.make_plan(self.db, precio_mensual=10000)
        tenant = fx.make_tenant(self.db)
        self.db.commit()

        with patch(
            "app.api.v1.super_admin.router.calculate_plan_vencimiento",
            side_effect=RuntimeError("boom: detalle interno que nunca debe llegar al cliente"),
        ):
            resp = self.client.patch(
                f"/api/v1/super-admin/tenants/{tenant.id}",
                json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
            )

        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assert_envelope(body, expected_code="INTERNAL_ERROR")
        self.assertNotIn("boom", body["detail"])
        self.assertNotIn("RuntimeError", body["detail"])


if __name__ == "__main__":
    unittest.main()
