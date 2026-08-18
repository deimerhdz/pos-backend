"""CONGELA comportamiento corregido: app/api/v1/promotions/router.py:37
(list_promotions) expone X-Server-Time — A-09 (registro-de-anomalias.md,
reapertura 2026-08-18, spec 023-correccion-zona-horaria-pos-staff).

Invoca list_promotions directamente como función Python (mismo patrón que
test_table_sessions_router.py): Depends(...) nunca se resuelve así, así que
basta con pasar `response=Response()`, `db` y un doble mínimo de usuario.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_router -v
"""
from datetime import datetime, timezone
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import Response

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.promotions import router as promotions_router


class TestListPromotionsA09(unittest.TestCase):
    def test_expone_x_server_time_en_utc(self):
        db = fx.new_session()
        user = SimpleNamespace(id="u1")  # doble mínimo de get_current_user
        response = Response()

        instant = datetime(2026, 8, 18, 22, 30, 5, tzinfo=timezone.utc)
        with mock.patch("app.api.v1.promotions.router.datetime") as mocked:
            mocked.now.return_value = instant
            promotions_router.list_promotions(
                response=response, page=1, size=20, status_filter=None,
                search=None, db=db, _=user,
            )

        self.assertEqual(response.headers["X-Server-Time"], instant.isoformat())

    def test_el_header_no_cambia_la_forma_de_la_respuesta(self):
        """El cuerpo (Page[PromotionResponse]) sigue igual: el header es
        aditivo, no reemplaza ni envuelve el body existente."""
        db = fx.new_session()
        fx.make_promotion(db, name="20% Granizados")
        db.commit()
        user = SimpleNamespace(id="u1")
        response = Response()

        page = promotions_router.list_promotions(
            response=response, page=1, size=20, status_filter=None,
            search=None, db=db, _=user,
        )

        self.assertEqual(page["total"], 1)
        self.assertIn("X-Server-Time", response.headers)


if __name__ == "__main__":
    unittest.main()
