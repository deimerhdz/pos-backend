"""CONGELA comportamiento corregido: app/api/v1/promotions/router.py
(list_promotions) expone X-Server-Time — A-09 (registro-de-anomalias.md,
reapertura 2026-08-18, spec 023-correccion-zona-horaria-pos-staff).

Reescrito para la spec 063 (modelo por conjunto explícito de variantes;
decisión de negocio A-58…A-65 en registro-de-anomalias.md): la respuesta pierde
`overlaps` (A-59) y `priority` (A-58), el enum de entrada se reduce a
`{percent, package_price}` (A-62) y `PromotionResponse` gana `variants` /
`condition_text` / `closed_by_refactor_at` (FR-005, FR-025). El header
`X-Server-Time` **no cambia**.

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


def _list(db, user, response, **kw):
    kw.setdefault("page", 1)
    kw.setdefault("size", 20)
    kw.setdefault("status_filter", None)
    kw.setdefault("search", None)
    kw.setdefault("closed_by_refactor", None)
    return promotions_router.list_promotions(response=response, db=db, _=user, **kw)


class TestListPromotionsA09(unittest.TestCase):
    def test_expone_x_server_time_en_utc(self):
        db = fx.new_session()
        user = SimpleNamespace(id="u1")  # doble mínimo de get_current_user
        response = Response()

        instant = datetime(2026, 8, 18, 22, 30, 5, tzinfo=timezone.utc)
        with mock.patch("app.api.v1.promotions.router.datetime") as mocked:
            mocked.now.return_value = instant
            _list(db, user, response)

        self.assertEqual(response.headers["X-Server-Time"], instant.isoformat())

    def test_el_header_no_cambia_la_forma_de_la_respuesta(self):
        """El cuerpo (Page[PromotionResponse]) sigue igual: el header es
        aditivo. spec 063: cada item trae `variants` / `condition_text` y ya no
        trae `overlaps` / `priority`."""
        db = fx.new_session()
        cat = fx.make_category(db)
        prod = fx.make_product(db, category=cat)
        variant = fx.make_variant(db, product=prod, price=8000)
        promo = fx.make_promotion(db, name="10% Granizados")
        fx.add_rule_to_promotion(db, promo, type="percent", value=10, min_qty=1, variants=[variant])
        db.commit()
        user = SimpleNamespace(id="u1")
        response = Response()

        page = _list(db, user, response)

        self.assertEqual(page["total"], 1)
        self.assertIn("X-Server-Time", response.headers)
        item = page["items"][0]
        self.assertNotIn("overlaps", item)
        self.assertNotIn("priority", item)
        self.assertNotIn("targets", item)
        self.assertNotIn("variants", item)  # spec 063 (2026-09-01): vive en cada regla
        regla = item["rules"][0]
        self.assertEqual(len(regla["variants"]), 1)
        self.assertEqual(regla["variants"][0]["unit_price"], 8000)
        self.assertEqual(regla["condition_text"], "10% en estas 1 variantes")

    def test_filtro_closed_by_refactor_lista_las_finalizadas_por_la_migracion(self):
        """FR-025: `?closed_by_refactor=true` filtra `closed_by_refactor_at IS NOT NULL`."""
        db = fx.new_session()
        viva = fx.make_promotion(db, name="viva")
        cerrada = fx.make_promotion(db, name="cerrada por refactor", status="finished")
        cerrada.closed_by_refactor_at = datetime(2026, 8, 31, 12, 0)
        db.commit()
        user = SimpleNamespace(id="u1")

        solo_cerradas = _list(db, user, Response(), closed_by_refactor=True)
        self.assertEqual({i["name"] for i in solo_cerradas["items"]}, {"cerrada por refactor"})

        solo_vivas = _list(db, user, Response(), closed_by_refactor=False)
        self.assertEqual({i["name"] for i in solo_vivas["items"]}, {"viva"})

        self.assertEqual(viva.id is not None, True)  # silencia linters sobre `viva`


if __name__ == "__main__":
    unittest.main()
