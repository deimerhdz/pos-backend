"""Tests de la nueva funcionalidad — spec 084-fix-promociones-productos, enmienda 2026-09-21
(A-83): duplicar una promoción con el nombre de otra ya existente **la reemplaza** (se elimina con
todas sus reglas y la copia ocupa su nombre), salvo que esté `active`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_duplicate_replace -v
"""
from decimal import Decimal
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.promotions import service
from app.api.v1.promotions.router import duplicate_promotion
from app.api.v1.promotions.schemas import PromotionDuplicate
from app.models.promotion import Promotion, PromotionRule, PromotionVariant


class DuplicarReemplazandoTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        prod = fx.make_product(self.db)
        self.v1 = fx.make_variant(self.db, product=prod, name="8 onzas", price=Decimal("8000"))
        self.v2 = fx.make_variant(self.db, product=prod, name="12 onzas", price=Decimal("10000"))
        self.fuente = fx.make_promotion(self.db, name="promo lunes", status="finished")
        fx.add_rule_to_promotion(
            self.db, self.fuente, type="package_price", value=Decimal("12000"), min_qty=2,
            variants=[self.v1],
        )
        fx.add_rule_to_promotion(
            self.db, self.fuente, type="package_price", value=Decimal("17000"), min_qty=2,
            variants=[self.v2],
        )
        self.db.commit()

    def _nombres(self):
        self.db.expire_all()
        return sorted(p.name for p in self.db.execute(select(Promotion)).scalars())

    def test_reemplaza_a_otra_promocion_con_ese_nombre(self):
        anterior = fx.make_promotion(self.db, name="promo lunes (copia)", status="draft")
        fx.add_rule_to_promotion(self.db, anterior, variants=[self.v1])
        self.db.commit()
        anterior_id = anterior.id

        copia = service.duplicate_replacing(self.db, self.fuente, "promo lunes (copia)", anterior)
        self.db.commit()

        self.assertEqual(copia.name, "promo lunes (copia)")
        self.assertEqual(copia.status, "draft")
        self.assertEqual(len(copia.rules), 2)  # las reglas de la fuente, no las de la anterior
        self.assertEqual(self._nombres(), ["promo lunes", "promo lunes (copia)"])
        self.assertIsNone(self.db.get(Promotion, anterior_id))

    def test_elimina_las_reglas_y_variantes_de_la_anterior(self):
        anterior = fx.make_promotion(self.db, name="dup", status="paused")
        regla = fx.add_rule_to_promotion(self.db, anterior, variants=[self.v1, self.v2])
        self.db.commit()
        regla_id = regla.id

        service.duplicate_replacing(self.db, self.fuente, "dup", anterior)
        self.db.commit()

        self.db.expire_all()
        self.assertIsNone(self.db.get(PromotionRule, regla_id))
        huerfanas = self.db.execute(
            select(PromotionVariant).where(PromotionVariant.promotion_rule_id == regla_id)
        ).scalars().all()
        self.assertEqual(huerfanas, [])

    def test_puede_reemplazarse_a_si_misma_conservando_el_nombre(self):
        """Duplicar con el mismo nombre de la fuente: la copia reemplaza a la original."""
        fuente_id = self.fuente.id

        copia = service.duplicate_replacing(self.db, self.fuente, "promo lunes", self.fuente)
        self.db.commit()

        self.assertNotEqual(copia.id, fuente_id)
        self.assertEqual(copia.name, "promo lunes")
        self.assertEqual(copia.status, "draft")
        self.assertEqual(len(copia.rules), 2)
        self.assertEqual(self._nombres(), ["promo lunes"])
        self.assertIsNone(self.db.get(Promotion, fuente_id))

    def test_no_reemplaza_una_promocion_activa(self):
        activa = fx.make_promotion(self.db, name="viva", status="active")
        fx.add_rule_to_promotion(self.db, activa, variants=[self.v1])
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            service.duplicate_replacing(self.db, self.fuente, "viva", activa)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("activa", ctx.exception.detail)
        self.db.rollback()
        self.assertEqual(self._nombres(), ["promo lunes", "viva"])  # nada cambió


class EndpointDuplicarTests(unittest.TestCase):
    """`POST /promotions/{id}/duplicate`: sin `replace_existing` un nombre repetido sigue siendo
    409; con él, reemplaza y deja auditoría de la promoción eliminada."""

    def setUp(self):
        self.db = fx.new_session()
        prod = fx.make_product(self.db)
        v = fx.make_variant(self.db, product=prod, name="8 onzas", price=Decimal("8000"))
        self.fuente = fx.make_promotion(self.db, name="promo lunes", status="finished")
        fx.add_rule_to_promotion(self.db, self.fuente, variants=[v])
        self.anterior = fx.make_promotion(self.db, name="promo lunes (copia)", status="draft")
        fx.add_rule_to_promotion(self.db, self.anterior, variants=[v])
        self.db.commit()
        self.user = SimpleNamespace(id=1)

    def _llamar(self, **body):
        with mock.patch("app.api.v1.promotions.router.record_audit") as audit:
            res = duplicate_promotion(
                self.fuente.id, PromotionDuplicate(name="promo lunes (copia)", **body),
                self.db, self.user,
            )
        return res, audit

    def test_sin_replace_existing_un_nombre_repetido_sigue_siendo_409(self):
        with self.assertRaises(HTTPException) as ctx:
            self._llamar()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "Ya existe una promoción con ese nombre")
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Promotion, self.anterior.id))

    def test_con_replace_existing_reemplaza_y_audita_la_eliminada(self):
        anterior_id = self.anterior.id

        res, audit = self._llamar(replace_existing=True)

        self.assertEqual(res["name"], "promo lunes (copia)")
        self.assertEqual(res["status"], "draft")
        self.db.expire_all()
        self.assertIsNone(self.db.get(Promotion, anterior_id))
        acciones = [c.kwargs["action"] for c in audit.call_args_list]
        self.assertEqual(acciones, ["delete", "duplicate"])

    def test_sin_conflicto_replace_existing_no_borra_nada(self):
        with mock.patch("app.api.v1.promotions.router.record_audit"):
            res = duplicate_promotion(
                self.fuente.id,
                PromotionDuplicate(name="nombre libre", replace_existing=True),
                self.db, self.user,
            )
        self.assertEqual(res["name"], "nombre libre")
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(Promotion, self.anterior.id))


if __name__ == "__main__":
    unittest.main()
