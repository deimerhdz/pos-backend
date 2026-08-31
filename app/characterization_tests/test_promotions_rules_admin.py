"""Tests de la nueva funcionalidad — spec 063-promociones-por-variante,
US1 (armar la promoción), US3 (solape real) y US5 (duplicar, editar activa,
estados). `contracts/administracion-promociones.md`.

Decisión de negocio: A-58…A-65 (registro-de-anomalias.md, 2026-08-31,
propietario del repositorio).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_rules_admin -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.promotions import service
from app.api.v1.promotions.schemas import (
    PromotionCreate, PromotionShapeUpdate, PromotionUpdate,
)

STARTS = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def _create_payload(**kw):
    kw.setdefault("name", "promo")
    kw.setdefault("type", "package_price")
    kw.setdefault("value", Decimal("12000"))
    kw.setdefault("min_qty", 2)
    kw.setdefault("starts_at", STARTS)
    return PromotionCreate(**kw)


class TestUS1ArmarPromocion(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.prod = fx.make_product(self.db)
        # 8 variantes "Pequeño con licor" a $8.000
        self.variantes = [
            fx.make_variant(self.db, product=self.prod, price=Decimal("8000"), name=f"licor-{i}")
            for i in range(8)
        ]
        self.db.commit()

    def _ids(self, n=8):
        return [v.id for v in self.variantes[:n]]

    def test_ca3_conjunto_vacio_rechazado(self):
        with self.assertRaises(ValidationError):
            _create_payload(variant_ids=[])

    def test_ca4_percent_mayor_a_100_rechazado(self):
        with self.assertRaises(ValidationError):
            _create_payload(type="percent", value=Decimal("150"), min_qty=1,
                            variant_ids=self._ids(1))

    def test_ca1_ca5_paquete_nace_borrador_con_condicion(self):
        promo = service.create(
            self.db,
            _create_payload(name="2X Pequeños con licor $12.000", variant_ids=self._ids()),
        )
        self.db.commit()
        data = service.serialize_promotion(self.db, promo)
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["condition_text"],
                         "Llevando 2 de estas 8 variantes pagas $12.000")
        self.assertEqual(len(data["variants"]), 8)
        self.assertTrue(all(v["unit_price"] == Decimal("8000") for v in data["variants"]))

    def test_ca2_una_variante_creada_despues_no_entra_sola(self):
        promo = service.create(self.db, _create_payload(variant_ids=self._ids()))
        self.db.commit()
        # una variante nueva en el mismo producto, DESPUÉS de guardar
        nueva = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"), name="nueva")
        self.db.commit()
        data = service.serialize_promotion(self.db, promo)
        ids = {v["product_variant_id"] for v in data["variants"]}
        self.assertNotIn(nueva.id, ids)

    def test_fr016_precio_de_paquete_sin_descuento_bloquea(self):
        barata = fx.make_variant(self.db, product=self.prod, price=Decimal("6000"), name="sin-licor")
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            service.create(
                self.db,
                _create_payload(value=Decimal("12000"), min_qty=2,
                                variant_ids=[self.variantes[0].id, barata.id]),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["cheapest_unit_price"], "6000.00")


class TestUS3SolapeReal(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        prod = fx.make_product(self.db)
        self.v = fx.make_variant(self.db, product=prod, price=Decimal("10000"), name="granizado")
        self.otra = fx.make_variant(self.db, product=prod, price=Decimal("9000"), name="otra")
        self.db.commit()

    def _activa(self, name, **kw):
        p = service.create(self.db, _create_payload(
            name=name, type="percent", value=Decimal("10"), min_qty=1,
            variant_ids=[self.v.id], **kw,
        ))
        service.change_status(self.db, p, "active")
        self.db.commit()
        return p

    def test_ca2_variante_compartida_ventanas_que_se_cruzan_bloquea(self):
        self._activa("10% en granizados")
        with self.assertRaises(HTTPException) as ctx:
            self._activa("20% en granizados")
        self.assertEqual(ctx.exception.status_code, 409)
        conflicto = ctx.exception.detail["conflicts"][0]
        self.assertEqual(conflicto["promotion_name"], "10% en granizados")
        self.assertIn(str(self.v.id), conflicto["variant_ids"])

    def test_ca3_ventanas_horarias_disjuntas_permitido(self):
        # quickstart §US3: "00:00–14:59" vs "15:00–cierre" → no se cruzan.
        self._activa("mañana", start_time=time(0, 0), end_time=time(14, 59))
        self._activa("tarde", start_time=time(15, 0), end_time=time(23, 0))  # no debe lanzar
        self.assertTrue(True)

    def test_ca5_dimension_abierta_se_cruza_con_franja(self):
        self._activa("sin franja")  # cubre todas las horas
        with self.assertRaises(HTTPException) as ctx:
            self._activa("con franja", start_time=time(15, 0), end_time=time(17, 0))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_ca4_ventana_que_cruza_medianoche_aceptada_al_guardar(self):
        p = service.create(self.db, _create_payload(
            name="happy hour", type="percent", value=Decimal("25"), min_qty=1,
            variant_ids=[self.v.id], start_time=time(22, 0), end_time=time(2, 0),
        ))
        self.db.commit()
        self.assertEqual(p.start_time, time(22, 0))


class TestUS5DuplicarEditarEstados(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        prod = fx.make_product(self.db)
        self.v = fx.make_variant(self.db, product=prod, price=Decimal("10000"), name="v")
        self.v2 = fx.make_variant(self.db, product=prod, price=Decimal("9000"), name="v2")
        self.db.commit()
        self.activa = service.create(self.db, _create_payload(
            name="activa", type="percent", value=Decimal("10"), min_qty=1,
            variant_ids=[self.v.id],
        ))
        service.change_status(self.db, self.activa, "active")
        self.db.commit()

    def test_ca1_editar_escalares_de_una_activa(self):
        service.update(self.db, self.activa, PromotionUpdate(
            name="activa renombrada", ends_at=datetime(2026, 12, 31, tzinfo=timezone.utc),
            days_of_week="0,1,2", start_time=time(9, 0), end_time=time(18, 0),
        ))
        self.db.commit()
        self.assertEqual(self.activa.name, "activa renombrada")
        self.assertEqual(self.activa.days_of_week, "0,1,2")

    def test_ca2_cambiar_valor_de_una_activa_bloquea(self):
        with self.assertRaises(HTTPException) as ctx:
            service.update(self.db, self.activa, PromotionUpdate(value=Decimal("20")))
        self.assertEqual(ctx.exception.status_code, 422)
        with self.assertRaises(HTTPException) as ctx:
            service.update_shape(self.db, self.activa,
                                 PromotionShapeUpdate(variant_ids=[self.v2.id]))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_ca3_reactivar_finalizada_409(self):
        service.change_status(self.db, self.activa, "finished")
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            service.change_status(self.db, self.activa, "active")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_ca4_duplicar_copia_borrador_con_mismo_conjunto(self):
        copia = service.duplicate(self.db, self.activa, "activa (copia)")
        self.db.commit()
        self.assertEqual(copia.status, "draft")
        self.assertEqual(copia.type, self.activa.type)
        self.assertEqual(copia.value, self.activa.value)
        self.assertEqual(
            {pv.product_variant_id for pv in copia.variants},
            {pv.product_variant_id for pv in self.activa.variants},
        )
        # cambiar el valor en la copia (draft) sí se permite
        service.update(self.db, copia, PromotionUpdate(value=Decimal("15")))
        self.db.commit()
        self.assertEqual(copia.value, Decimal("15"))


if __name__ == "__main__":
    unittest.main()
