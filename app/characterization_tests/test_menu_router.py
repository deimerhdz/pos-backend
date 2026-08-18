"""CONGELA comportamiento corregido: app/api/v1/menu/router.py:82
(_build_menu) — cierra la anomalía A-08 (specs/000-reconocimiento/
registro-de-anomalias.md), por contraste directo con A-07.

Antes de esta corrección (specs/022-correccion-zona-horaria-menu-carrito),
`_build_menu` evaluaba la vigencia de promociones con
`datetime.now(timezone.utc).replace(tzinfo=None)` — un naive que en realidad
es UTC, pero que `promotions.local_now()` interpreta como si ya estuviera en
hora local del tenant. Con `TENANT_TIMEZONE=America/Bogota` (UTC-5), esto
podía mostrar una promoción vigente/no vigente hasta 5 horas antes o después
de su ventana real.

Esta spec corrige el punto de invocación para pasar un `datetime` aware,
igual que ya hacen los cuatro caminos de cobro real (A-07).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_menu_router -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.menu.router import _build_menu


class TestBuildMenuA08(unittest.TestCase):
    def _seed(self, db):
        category = fx.make_category(db)
        product = fx.make_product(db, category=category)
        variant = fx.make_variant(db, product=product, price=Decimal("10000"))
        fx.make_promotion(
            db, type="percent", value=Decimal("20"), status="active",
            start_time=time(20, 0), end_time=time(21, 0),
        )
        db.commit()
        return variant

    def test_a08_fuera_de_ventana_en_hora_local_no_descuenta(self):
        """FR-001/CA1: a las 20:00 UTC (15:00 Bogotá, fuera de la ventana
        20:00-21:00 local) el menú NO debe mostrar la promoción vigente."""
        db = fx.new_session()
        self._seed(db)

        instant = datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        with fx.frozen_now(instant, module="app.api.v1.menu.router"):
            menu = _build_menu(db)

        variant_resp = menu[0].products[0].variants[0]
        self.assertIsNone(variant_resp.discounted_price)

    def test_a08_dentro_de_ventana_en_hora_local_si_descuenta(self):
        """CA3 (sin regresión): a la 01:00 UTC del día siguiente (20:00
        Bogotá, dentro de la ventana 20:00-21:00 local) el menú SÍ debe
        mostrar la promoción vigente — mismo resultado que hoy en el caso
        correcto."""
        db = fx.new_session()
        self._seed(db)

        instant = datetime(2026, 1, 16, 1, 0, tzinfo=timezone.utc)
        with fx.frozen_now(instant, module="app.api.v1.menu.router"):
            menu = _build_menu(db)

        variant_resp = menu[0].products[0].variants[0]
        self.assertIsNotNone(variant_resp.discounted_price)
        self.assertLess(variant_resp.discounted_price, variant_resp.price)


if __name__ == "__main__":
    unittest.main()
