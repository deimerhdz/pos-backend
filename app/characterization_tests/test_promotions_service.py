"""Tests de la nueva funcionalidad — spec 063-promociones-por-variante, US2:
el motor `evaluate_variant_sets` (contracts/motor-y-persistencia.md §2).

Decisión de negocio: A-58…A-65 (registro-de-anomalias.md, 2026-08-31,
propietario del repositorio). Precios del catálogo real (spec.md §Assumptions).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_service -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.promotions import service as promotions

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)  # miércoles, 13:00 Bogotá


def _line(variant, qty, *, active=True, combo_id=None):
    return {
        "product_variant_id": variant.id,
        "unit_price": Decimal(variant.price),
        "quantity": qty,
        "line_id": variant.id,   # una fila por variante en estos escenarios
        "combo_id": combo_id,
        "_variant_active": active,
        "description": variant.name,
    }


class TestEvaluateVariantSets(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.prod = fx.make_product(self.db)

    def _variant(self, price, name=None):
        return fx.make_variant(
            self.db, product=self.prod, price=Decimal(str(price)),
            name=name or f"v-{uuid4()}",
        )

    def _promo(self, type_, value, min_qty, variants, **kw):
        """spec 063 (revisión 2026-09-01): la promoción solo lleva vigencia y
        estado; tipo/valor/cantidad mínima/conjunto viven en su única regla
        (`add_rule_to_promotion`, contracts/migracion.md §2.1). Devuelve la
        `Promotion` (no la regla) porque `AppliedPromotion.promotion_id` es
        el id de la promoción, no el de la regla."""
        p = fx.make_promotion(
            self.db, status="active",
            start_time=kw.get("start_time"), end_time=kw.get("end_time"),
            days_of_week=kw.get("days_of_week"),
        )
        fx.add_rule_to_promotion(
            self.db, p, type=type_, value=Decimal(str(value)), min_qty=min_qty,
            variants=variants,
        )
        self.db.commit()
        return p

    # ---- CA1 / SC-008: 2 variantes distintas del conjunto -> $12.000 ----
    def test_1_paquete_combina_dos_variantes_distintas(self):
        a = self._variant(8000, "Ojo de Diablo Pequeño")
        b = self._variant(8000, "Perla Negra Pequeño")
        self._promo("package_price", 12000, 2, [a, b])

        r = promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], NOW)
        self.assertEqual(r.total, Decimal("4000.00"))  # 16000 - 12000

    # ---- CA2: 3 unidades -> 1 grupo + 1 suelta a precio normal ----
    def test_2_tres_unidades_una_suelta_determinista(self):
        a, b, c = self._variant(8000, "a"), self._variant(8000, "b"), self._variant(8000, "c")
        self._promo("package_price", 12000, 2, [a, b, c])

        r = promotions.evaluate_variant_sets(
            self.db, [_line(a, 1), _line(b, 1), _line(c, 1)], NOW,
        )
        # 1 grupo (2 unidades más caras) -> descuenta 4000; la 3ª a precio normal.
        self.assertEqual(r.total, Decimal("4000.00"))

    # ---- CA3 / SC-005: otro orden de captura -> total y reparto idénticos ----
    def test_3_mismo_pedido_otro_orden_identico(self):
        a, b, c = self._variant(8000, "a"), self._variant(8000, "b"), self._variant(8000, "c")
        self._promo("package_price", 12000, 2, [a, b, c])

        r1 = promotions.evaluate_variant_sets(
            self.db, [_line(a, 1), _line(b, 1), _line(c, 1)], NOW,
        )
        r2 = promotions.evaluate_variant_sets(
            self.db, [_line(c, 1), _line(a, 1), _line(b, 1)], NOW,
        )
        self.assertEqual(r1.total, r2.total)

    # ---- CA4: percent min_qty 1 sobre conjunto mixto ----
    def test_4_percent_min_qty_1_mixto(self):
        grande = self._variant(15000, "Grande con licor")
        mediano = self._variant(8000, "Mediano sin licor")
        self._promo("percent", 10, 1, [grande, mediano])

        r = promotions.evaluate_variant_sets(
            self.db, [_line(grande, 1), _line(mediano, 1)], NOW,
        )
        # 10% de (15000+8000) = 2300 -> total cobrado 20700
        self.assertEqual(r.total, Decimal("2300.00"))

    # ---- CA5 / SC-005: "15% llevando 3 medianos" -> $4.500, reparto -3300/-1200 ----
    def test_5_percent_min_qty_3_reparto(self):
        con = self._variant(11000, "Mediano con licor")
        sin = self._variant(8000, "Mediano sin licor")
        self._promo("percent", 15, 3, [con, sin])

        lines = [_line(con, 2), _line(sin, 2)]
        r = promotions.evaluate_variant_sets(self.db, lines, NOW)
        # grupo = 3 más caras: 11000 + 11000 + 8000 = 30000; 15% = 4500.
        self.assertEqual(r.total, Decimal("4500.00"))
        # reparto por importe cobrado: línea "con" -3300, línea "sin" -1200.
        by_line = {i: v for i, v in r.by_line.items()}
        self.assertEqual(sorted(by_line.values()), [Decimal("1200"), Decimal("3300")])

    # ---- CA6: no alcanza min_qty -> sin descuento ----
    def test_6_no_alcanza_el_minimo(self):
        a, b = self._variant(8000, "a"), self._variant(8000, "b")
        self._promo("package_price", 12000, 3, [a, b])
        r = promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], NOW)
        self.assertEqual(r.total, Decimal("0"))
        self.assertEqual(r.applied, [])

    # ---- CA7: día no incluido ----
    def test_7_dia_no_incluido_sin_descuento(self):
        a, b = self._variant(8000, "a"), self._variant(8000, "b")
        self._promo("package_price", 12000, 2, [a, b], days_of_week="0")  # solo lunes
        r = promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], NOW)
        self.assertEqual(r.total, Decimal("0"))

    # ---- CA8: ventana horaria 14:59 vs 15:01 ----
    def test_8_ventana_horaria(self):
        a, b = self._variant(8000, "a"), self._variant(8000, "b")
        self._promo("package_price", 12000, 2, [a, b],
                    start_time=time(15, 0), end_time=time(17, 0))
        antes = datetime(2026, 8, 5, 19, 59, tzinfo=timezone.utc)   # 14:59 Bogotá
        despues = datetime(2026, 8, 5, 20, 1, tzinfo=timezone.utc)  # 15:01 Bogotá
        self.assertEqual(
            promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], antes).total,
            Decimal("0"),
        )
        self.assertEqual(
            promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], despues).total,
            Decimal("4000.00"),
        )

    # ---- CA9 / FR-011: variante desactivada no cuenta ----
    def test_10_variante_desactivada_no_es_unidad_elegible(self):
        a, b = self._variant(8000, "a"), self._variant(8000, "b")
        self._promo("package_price", 12000, 2, [a, b])
        r = promotions.evaluate_variant_sets(
            self.db, [_line(a, 1), _line(b, 1, active=False)], NOW,
        )
        self.assertEqual(r.total, Decimal("0"))  # solo 1 unidad elegible -> 0 grupos

    # ---- SC-005: división no exacta cuadra al peso en cualquier orden ----
    def test_9_division_no_exacta_residuo_al_peso(self):
        a = self._variant(6000, "a")
        b = self._variant(6000, "b")
        c = self._variant(6000, "c")
        self._promo("package_price", 16000, 3, [a, b, c])
        top = max((a, b, c), key=lambda v: str(v.id))

        resultados = []
        for orden in ([a, b, c], [c, b, a], [b, c, a]):
            promo_lines = [_line(v, 1) for v in orden]
            r = promotions.evaluate_variant_sets(self.db, promo_lines, NOW)
            self.assertEqual(r.total, Decimal("2000.00"))              # 18000 - 16000
            self.assertEqual(sum(r.by_line.values()), Decimal("2000"))  # cuadra al peso
            por_variante = {orden[i].id: d for i, d in r.by_line.items()}
            resultados.append(por_variante)
            # la variante de id más alto cobra el residuo ($5.334) -> descuenta
            # menos ($666); las otras dos descuentan $667 (research.md D6).
            self.assertEqual(por_variante[top.id], Decimal("666"))
            self.assertEqual(
                sorted(por_variante.values()),
                [Decimal("666"), Decimal("667"), Decimal("667")],
            )
        # el reparto por variante NO depende del orden de las líneas.
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(resultados[1], resultados[2])

    # ---- Assumptions: conjunto mixto con precios distintos -> $18.000 ----
    def test_11_conjunto_mixto_precios_distintos(self):
        p8a = self._variant(8000, "Ojo de Diablo")
        p6 = self._variant(6000, "Manzana Verde sin licor")
        p8b = self._variant(8000, "Perla Negra")
        self._promo("package_price", 12000, 2, [p8a, p6, p8b])

        r = promotions.evaluate_variant_sets(
            self.db, [_line(p8a, 1), _line(p6, 1), _line(p8b, 1)], NOW,
        )
        # el grupo toma las 2 de 8000 -> 16000 - 12000 = 4000; la de 6000 suelta.
        self.assertEqual(r.total, Decimal("4000.00"))

    # ---- FR-009: un grupo nunca encarece (descuento topado en 0) ----
    def test_12_package_price_no_encarece(self):
        a, b = self._variant(5000, "a"), self._variant(5000, "b")
        self._promo("package_price", 12000, 2, [a, b])  # 12000 > 10000 normal
        r = promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], NOW)
        self.assertEqual(r.total, Decimal("0"))

    # ---- applied lista toda promoción con amount > 0, ordenada por promotion_id ----
    def test_13_applied_promotions_agregado_por_promocion(self):
        a = self._variant(8000, "a")
        b = self._variant(6000, "b")
        p1 = self._promo("percent", 10, 1, [a])
        p2 = self._promo("percent", 20, 1, [b])
        r = promotions.evaluate_variant_sets(self.db, [_line(a, 1), _line(b, 1)], NOW)
        montos = {ap.promotion_id: ap.amount for ap in r.applied}
        self.assertEqual(montos[p1.id], Decimal("800.00"))
        self.assertEqual(montos[p2.id], Decimal("1200.00"))
        self.assertEqual([ap.promotion_id for ap in r.applied],
                         sorted([p1.id, p2.id], key=str))

    # ---- T027 (spec 063, revisión 2026-09-01): dos reglas de la MISMA
    # promoción, ambas con descuento en el mismo cobro -> dos entradas en
    # `applied` con igual `promotion_id` y distinto `rule_id` ----
    def test_14_dos_reglas_de_la_misma_promocion_descuentan_en_el_mismo_cobro(self):
        pequenos_a = self._variant(8000, "pequeño-a")
        pequenos_b = self._variant(8000, "pequeño-b")
        medianos_a = self._variant(11000, "mediano-a")
        medianos_b = self._variant(11000, "mediano-b")
        promo = fx.make_promotion(self.db, name="2X entre semana", status="active")
        regla_pequenos = fx.add_rule_to_promotion(
            self.db, promo, type="package_price", value=Decimal("12000"), min_qty=2,
            variants=[pequenos_a, pequenos_b],
        )
        regla_medianos = fx.add_rule_to_promotion(
            self.db, promo, type="package_price", value=Decimal("17000"), min_qty=2,
            variants=[medianos_a, medianos_b],
        )
        self.db.commit()

        r = promotions.evaluate_variant_sets(self.db, [
            _line(pequenos_a, 1), _line(pequenos_b, 1),
            _line(medianos_a, 1), _line(medianos_b, 1),
        ], NOW)

        # $16.000 -> $12.000 (regla Pequeños) + $22.000 -> $17.000 (regla Medianos)
        self.assertEqual(r.total, Decimal("9000.00"))
        self.assertEqual(len(r.applied), 2)
        self.assertTrue(all(ap.promotion_id == promo.id for ap in r.applied))
        rule_ids = {ap.rule_id for ap in r.applied}
        self.assertEqual(rule_ids, {regla_pequenos.id, regla_medianos.id})
        montos = {ap.rule_id: ap.amount for ap in r.applied}
        self.assertEqual(montos[regla_pequenos.id], Decimal("4000.00"))
        self.assertEqual(montos[regla_medianos.id], Decimal("5000.00"))


if __name__ == "__main__":
    unittest.main()
