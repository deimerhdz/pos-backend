"""El middleware/handlers de errores de super-admin no afectan otras rutas
(spec 068, Historia de Usuario 1 — "no revela"/no cambia nada fuera del
módulo; research.md § 7).

No son characterization tests: verifican infraestructura nueva
(`RequestIdMiddleware`/`register_error_handlers`), no comportamiento
heredado de otro módulo. Usa una ruta de control sintética
(`super_admin_http_fixtures.CONTROL_PREFIX`, fuera del prefijo de
super-admin) en vez de un router real de otro módulo: lo que se verifica es
que el mecanismo respeta el prefijo, no el comportamiento de negocio de
ningún otro módulo en particular — traer un router real solo añadiría
fixtures irrelevantes para lo que este test comprueba.

    python -m unittest app.characterization_tests.test_error_middleware_scope -v
"""
from __future__ import annotations

import unittest

from starlette.testclient import TestClient

from app.characterization_tests import super_admin_http_fixtures as hf


class ErrorMiddlewareScopeTests(unittest.TestCase):
    def setUp(self):
        self.db = hf.new_session()
        self.app = hf.build_app(self.db)
        self.client = TestClient(self.app)

    def test_ruta_fuera_del_modulo_conserva_la_respuesta_plana_de_hoy(self):
        resp = self.client.get(f"{hf.CONTROL_PREFIX}/no-existe")

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        # Forma de hoy para cualquier módulo sin este spec: solo `detail`.
        self.assertEqual(body, {"detail": "Control not found"})
        # El middleware/handlers nuevos no se activaron para esta ruta.
        self.assertNotIn("success", body)
        self.assertNotIn("error", body)
        self.assertNotIn("request_id", body)


if __name__ == "__main__":
    unittest.main()
