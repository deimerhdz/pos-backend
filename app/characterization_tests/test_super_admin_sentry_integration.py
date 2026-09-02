"""Integración con Sentry del módulo super-admin (spec 068, Historia de
Usuario 2).

Alcance de estos tests: la puerta de entorno propia de
`app/core/error_middleware.py::_report_unexpected_exception` (solo reporta
en producción, solo fallas no anticipadas) — verificable sin infraestructura
externa. **No** cubren la puerta de `sentry_sdk.init(...)` de
`app/main.py::create_app()` (solo se llama con `ENVIRONMENT == "prod"` y
`SENTRY_DSN` configurado): `app.main` construye la app real
(`initialize_database()`, `token_blocklist.ping()` en el lifespan) y exige
Postgres/Redis reales desde su primera línea — el mismo motivo, ya
documentado en `test_tenant_plan_assignment.py`, por el que `tenant_create()`
tampoco se prueba contra SQLite en este paquete. Esa puerta se verifica por
revisión de código y con el paso 3 de `quickstart.md` contra un entorno real.

No son characterization tests: la integración con Sentry es comportamiento
enteramente nuevo.

    python -m unittest app.characterization_tests.test_super_admin_sentry_integration -v
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from app.characterization_tests import plan_fixtures as fx
from app.characterization_tests import super_admin_http_fixtures as hf
from app.core.config import settings


class SuperAdminSentryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.db = hf.new_session()
        self.admin = hf.make_super_admin(self.db)
        self.db.commit()
        self.app = hf.build_app(self.db, token_data=hf.super_admin_token_data(self.admin))
        self.client = TestClient(self.app)

    def _force_unexpected_failure(self):
        plan = fx.make_plan(self.db, precio_mensual=10000)
        tenant = fx.make_tenant(self.db)
        self.db.commit()
        with patch(
            "app.api.v1.super_admin.router.calculate_plan_vencimiento",
            side_effect=RuntimeError("boom"),
        ):
            return self.client.patch(
                f"/api/v1/super-admin/tenants/{tenant.id}",
                json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
            )

    # ---------------------------------------------------------- (a) prod + falla inesperada

    def test_falla_inesperada_en_produccion_reporta_una_sola_vez(self):
        with patch.object(settings, "ENVIRONMENT", "prod"), patch(
            "app.core.error_middleware.sentry_sdk.capture_exception"
        ) as mock_capture:
            resp = self._force_unexpected_failure()

        self.assertEqual(resp.status_code, 500)
        mock_capture.assert_called_once()

    # ---------------------------------------------------------- (b) prod + error esperado

    def test_error_de_negocio_esperado_en_produccion_no_reporta_a_sentry(self):
        plan = fx.make_plan(self.db, precio_mensual=10000)
        self.db.commit()
        with patch.object(settings, "ENVIRONMENT", "prod"), patch(
            "app.core.error_middleware.sentry_sdk.capture_exception"
        ) as mock_capture:
            resp = self.client.patch(
                "/api/v1/super-admin/tenants/999999",
                json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
            )

        self.assertEqual(resp.status_code, 404)
        mock_capture.assert_not_called()

    # ---------------------------------------------------------- (c) fuera de producción

    def test_fuera_de_produccion_nunca_reporta_ni_lo_esperado_ni_lo_inesperado(self):
        self.assertNotEqual(settings.ENVIRONMENT, "prod")  # valor por defecto del repo ("dev")

        with patch("app.core.error_middleware.sentry_sdk.capture_exception") as mock_capture:
            unexpected_resp = self._force_unexpected_failure()
            self.assertEqual(unexpected_resp.status_code, 500)

            plan = fx.make_plan(self.db, precio_mensual=10000)
            self.db.commit()
            expected_resp = self.client.patch(
                "/api/v1/super-admin/tenants/999999",
                json={"plan_id": str(plan.id), "ciclo_facturacion": "mensual"},
            )
            self.assertEqual(expected_resp.status_code, 404)

        mock_capture.assert_not_called()

    # ---------------------------------------------------------- (d) el módulo funciona sin Sentry

    def test_responde_con_normalidad_sin_sentry_inicializado(self):
        """`build_app` nunca llama `sentry_sdk.init()` (no importa `app.main`,
        ver módulo docstring) — cualquier respuesta de este archivo ya
        demuestra FR-011. Este test lo deja explícito para esta historia."""
        resp = self._force_unexpected_failure()
        self.assertEqual(resp.status_code, 500)
        self.assertIn("request_id", resp.json())


if __name__ == "__main__":
    unittest.main()
