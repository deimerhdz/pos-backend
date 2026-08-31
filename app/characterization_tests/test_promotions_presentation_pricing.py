"""spec 040 — US2 (el cajero cobra pedidos que combinan productos de una misma
presentación) + US4 (entrada automática de variantes nuevas).

Ejercita `presentation_package_discount_for_lines` / `combined_discount_detailed`
directamente sobre listas de `promo_lines` (dicts, la forma que produce
`checkout.promo_lines_for`), y un par de escenarios de punta a punta vía
`checkout.pay_order`.

    python -m unittest app.characterization_tests.test_promotions_presentation_pricing -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest

from app.characterization_tests import presentation_fixtures as fx
from app.api.v1.promotions import service as svc


NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)  # miércoles, hora cualquiera


def _pl(variant, *, qty, presentation=None, combo_id=None, active=True, line_id=None):
    """Un `promo_line` con la forma de `checkout.promo_lines_for`."""
    return {
        "product_id": variant.product_id,
        "category_id": None,
        "quantity": qty,
        "line_total": Decimal(variant.price) * qty,
        "unit_price": Decimal(variant.price),
        "combo_id": combo_id,
        "product_variant_id": variant.id,
        "line_id": line_id,
        "presentation_id": (presentation.id if presentation is not None else variant.presentation_id),
        "_variant_active": active,
    }


class TestPresentationPricingUS2(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.p8 = fx.make_presentation(self.db, name="8oz")
        self.p16 = fx.make_presentation(self.db, name="16oz")
        # tres sabores en 8oz a $7.000; dos en 16oz a $9.500
        self.a8 = self._variant("Ojo de Diablo 8oz", "7000", self.p8)
        self.b8 = self._variant("Fresa Boom 8oz", "7000", self.p8)
        self.c8 = self._variant("Maracumango 8oz", "7000", self.p8)
        self.a16 = self._variant("Ojo de Diablo 16oz", "9500", self.p16)
        self.b16 = self._variant("Fresa Boom 16oz", "9500", self.p16)
        self.promo = fx.make_promotion(
            self.db, name="Precio por presentación", type="qty_price_presentation",
            status="active", value=0,
        )
        fx.make_presentation_rule(self.db, self.promo, self.p8, min_qty=2, pack_price="12000")
        self.db.commit()

    def _variant(self, name, price, presentation):
        prod = fx.make_product(self.db, name=name)
        v = fx.make_variant(self.db, product=prod, name=name, price=price)
        fx.assign_presentation(self.db, v, presentation)
        return v

    def _combined(self, lines):
        return svc.combined_discount_detailed(self.db, lines, NOW)

    # -- escenarios de la tabla de quickstart.md §US2 --------------------------

    def test_1_paquete_con_productos_distintos(self):
        r = self._combined([_pl(self.a8, qty=1), _pl(self.b8, qty=1)])
        self.assertEqual(r.total, Decimal("2000.00"))  # 14000 - 12000
        self.assertEqual(r.promotion_id, self.promo.id)

    def test_2_tres_unidades_reparto_determinista(self):
        lines = [_pl(self.a8, qty=1), _pl(self.b8, qty=1), _pl(self.c8, qty=1)]
        r = self._combined(lines)
        self.assertEqual(r.total, Decimal("2000.00"))  # 21000 - 19000
        pres = svc.presentation_package_discount_for_lines(self.db, lines, NOW)
        # la suelta se decide por identificador de variante más alto
        highest = max(range(3), key=lambda i: str(lines[i]["product_variant_id"]))
        self.assertEqual(pres.by_line.get(highest, Decimal(0)), Decimal(0))
        self.assertEqual(sum(pres.by_line.values()), Decimal("2000"))

    def test_3_mismo_pedido_otro_orden_identico(self):
        base = [_pl(self.a8, qty=1), _pl(self.b8, qty=1), _pl(self.c8, qty=1)]
        rev = list(reversed(base))
        p1 = svc.presentation_package_discount_for_lines(self.db, base, NOW)
        p2 = svc.presentation_package_discount_for_lines(self.db, rev, NOW)
        self.assertEqual(p1.total, p2.total)
        # el reparto por variante coincide (reindexado)
        by_variant_1 = {base[i]["product_variant_id"]: v for i, v in p1.by_line.items()}
        by_variant_2 = {rev[i]["product_variant_id"]: v for i, v in p2.by_line.items()}
        self.assertEqual(by_variant_1, by_variant_2)

    def test_4_dos_presentaciones_no_se_mezclan(self):
        fx.make_presentation_rule(self.db, self.promo, self.p16, min_qty=2, pack_price="16500")
        self.db.commit()
        r = self._combined([
            _pl(self.a8, qty=1), _pl(self.b8, qty=1),
            _pl(self.a16, qty=1), _pl(self.b16, qty=1),
        ])
        # 8oz: 14000 -> 12000 (2000); 16oz: 19000 -> 16500 (2500)
        self.assertEqual(r.total, Decimal("4500.00"))

    def test_5_cinco_unidades_dos_paquetes_y_suelta(self):
        r = self._combined([_pl(self.a8, qty=2), _pl(self.b8, qty=2), _pl(self.c8, qty=1)])
        # 35000 -> 2*12000 + 7000 = 31000
        self.assertEqual(r.total, Decimal("4000.00"))

    def test_6_ninguna_alcanza_el_minimo(self):
        fx.make_presentation_rule(self.db, self.promo, self.p16, min_qty=2, pack_price="16500")
        self.db.commit()
        r = self._combined([_pl(self.a8, qty=1), _pl(self.a16, qty=1)])
        self.assertEqual(r.total, Decimal("0.00"))
        self.assertIsNone(r.promotion_id)

    def test_7_dia_no_incluido_sin_descuento(self):
        self.promo.days_of_week = "0"  # solo lunes; NOW es miércoles
        self.db.commit()
        r = self._combined([_pl(self.a8, qty=1), _pl(self.b8, qty=1)])
        self.assertEqual(r.total, Decimal("0.00"))

    def test_8_ventana_horaria(self):
        self.promo.start_time = time(8, 0)
        self.promo.end_time = time(22, 0)
        self.db.commit()
        lines = [_pl(self.a8, qty=1), _pl(self.b8, qty=1)]
        antes = datetime(2026, 8, 5, 12, 59, tzinfo=timezone.utc)   # 07:59 local
        despues = datetime(2026, 8, 5, 13, 1, tzinfo=timezone.utc)  # 08:01 local
        self.assertEqual(
            svc.combined_discount_detailed(self.db, lines, antes).total, Decimal("0.00")
        )
        self.assertEqual(
            svc.combined_discount_detailed(self.db, lines, despues).total, Decimal("2000.00")
        )

    def test_9_division_no_exacta_residuo_al_peso(self):
        """CL-9 / SC-005: '3 × 8oz por $10.000' -> total $10.000 exacto, residuo
        de $1 a la línea de identificador de variante más alto, en cualquier orden."""
        db = fx.new_session()
        p8 = fx.make_presentation(db, name="8oz")

        def v(name):
            prod = fx.make_product(db, name=name)
            var = fx.make_variant(db, product=prod, name=name, price="7000")
            fx.assign_presentation(db, var, p8)
            return var

        a8, b8, c8 = v("A 8oz"), v("B 8oz"), v("C 8oz")
        promo = fx.make_promotion(
            db, name="3x8oz", type="qty_price_presentation", status="active", value=0,
        )
        fx.make_presentation_rule(db, promo, p8, min_qty=3, pack_price="10000")
        db.commit()

        lines = [_pl(a8, qty=1), _pl(b8, qty=1), _pl(c8, qty=1)]
        for orden in ([0, 1, 2], [2, 1, 0], [1, 2, 0]):
            ordered = [lines[i] for i in orden]
            pres = svc.presentation_package_discount_for_lines(db, ordered, NOW)
            self.assertEqual(pres.total, Decimal("11000.00"))  # 21000 - 10000
            self.assertEqual(sum(pres.by_line.values()), Decimal("11000"))
            highest = max(range(3), key=lambda i: str(ordered[i]["product_variant_id"]))
            # esa línea paga $3.334 -> descuento 7000 - 3334 = 3666
            self.assertEqual(pres.by_line[highest], Decimal("3666"))

    def test_10_variante_desactivada_no_es_unidad_elegible(self):
        """CL-1c / FR-015: 1 activa + 1 con variante `active=false` -> 0 paquetes."""
        r = self._combined([
            _pl(self.a8, qty=1),
            _pl(self.b8, qty=1, active=False),
        ])
        self.assertEqual(r.total, Decimal("0.00"))  # total 14000, sin paquete

    def test_sc005_suma_cuadra_al_peso_cualquier_orden(self):
        fx.make_presentation_rule(self.db, self.promo, self.p16, min_qty=2, pack_price="16500")
        self.db.commit()
        lines = [
            _pl(self.a8, qty=1), _pl(self.b8, qty=1), _pl(self.c8, qty=1),
            _pl(self.a16, qty=1), _pl(self.b16, qty=1),
        ]
        pres = svc.presentation_package_discount_for_lines(self.db, lines, NOW)
        self.assertEqual(sum(pres.by_line.values()), pres.total)


class TestPresentationAutoEntryUS4(unittest.TestCase):
    """US4 CA-9 / FR-019: una variante creada DESPUÉS de activar la promoción
    entra al paquete en el siguiente cobro, sin editar la promoción."""

    def test_variante_nueva_entra_por_referencia(self):
        db = fx.new_session()
        p8 = fx.make_presentation(db, name="8oz")
        prod_a = fx.make_product(db, name="A")
        va = fx.make_variant(db, product=prod_a, name="A 8oz", price="7000")
        fx.assign_presentation(db, va, p8)
        promo = fx.make_promotion(
            db, name="pres", type="qty_price_presentation", status="active", value=0,
        )
        fx.make_presentation_rule(db, promo, p8, min_qty=2, pack_price="12000")
        db.commit()

        # variante NUEVA creada después, con la misma presentación
        prod_b = fx.make_product(db, name="B")
        vb = fx.make_variant(db, product=prod_b, name="B 8oz", price="7000")
        fx.assign_presentation(db, vb, p8)
        db.commit()

        r = svc.combined_discount_detailed(
            db,
            [_pl(va, qty=1, presentation=p8), _pl(vb, qty=1, presentation=p8)],
            NOW,
        )
        self.assertEqual(r.total, Decimal("2000.00"))


if __name__ == "__main__":
    unittest.main()
