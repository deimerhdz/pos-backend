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
from app.api.v1.menu.router import _build_menu, _build_menu_promotions


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


class TestMenuPromotionsAnnouncementUS5(unittest.TestCase):
    """spec 040 — US5 / FR-021 / SC-006: el menú QR anuncia las promociones de
    precio por presentación **vigentes en ese instante**. Casos NUEVOS — no se
    toca `_build_menu` ni los `test_a08_*` de arriba (research.md D12)."""

    def _seed(self, db, *, days_of_week=None, start_time=None, end_time=None):
        p8 = fx.make_presentation(db, name="8oz")
        prod = fx.make_product(db)
        v = fx.make_variant(db, product=prod, name="8oz", price=Decimal("7000"))
        fx.assign_presentation(db, v, p8)
        promo = fx.make_promotion(
            db, name="2 x 8oz por 12.000", type="qty_price_presentation",
            status="active", value=Decimal("0"),
            days_of_week=days_of_week, start_time=start_time, end_time=end_time,
        )
        fx.make_presentation_rule(db, promo, p8, min_qty=2, pack_price="12000")
        db.commit()
        return promo

    def test_vigente_se_anuncia_con_texto_legible(self):
        db = fx.new_session()
        self._seed(db)
        anuncios = _build_menu_promotions(db, datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(len(anuncios), 1)
        regla = anuncios[0].rules[0]
        self.assertEqual(
            regla.text,
            "Llevando 2 de cualquier sabor en presentación 8oz por $12.000",
        )

    def test_fuera_de_ventana_de_dia_no_se_anuncia(self):
        db = fx.new_session()
        self._seed(db, days_of_week="0")  # solo lunes
        # miércoles 5 de agosto de 2026
        anuncios = _build_menu_promotions(db, datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc))
        self.assertEqual(anuncios, [])

    def test_fuera_de_ventana_de_hora_no_se_anuncia(self):
        db = fx.new_session()
        self._seed(db, start_time=time(8, 0), end_time=time(22, 0))
        # 07:00 local (12:00 UTC): fuera de la ventana
        anuncios = _build_menu_promotions(db, datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(anuncios, [])

    def test_vigencia_en_zona_del_tenant_no_arrastra_a08(self):
        """La ventana 20:00-22:00 local: a las 21:00 Bogotá (02:00 UTC del día
        siguiente) se anuncia — se evalúa en hora local, no en UTC."""
        db = fx.new_session()
        self._seed(db, start_time=time(20, 0), end_time=time(22, 0))
        anuncios = _build_menu_promotions(db, datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc))
        self.assertEqual(len(anuncios), 1)


if __name__ == "__main__":
    unittest.main()
