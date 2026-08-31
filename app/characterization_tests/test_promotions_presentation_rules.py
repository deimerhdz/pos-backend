"""spec 040 — US1 (configurar promociones con reglas por presentación) y US3
(avisos de precio no uniforme / "no es descuento real").

Invoca las funciones del router de promociones directamente como funciones
Python (mismo patrón que `test_promotions_router.py`): `Depends(...)` nunca se
resuelve así, se pasan `db` y un doble mínimo de usuario.

    python -m unittest app.characterization_tests.test_promotions_presentation_rules -v
"""
from datetime import datetime, time, timezone
from decimal import Decimal
import unittest

from fastapi import HTTPException

from app.characterization_tests import presentation_fixtures as fx
from app.api.v1.promotions import router as promo_router
from app.api.v1.promotions import service as promo_service
from app.api.v1.promotions.schemas import (
    PromotionCreate, PromotionShapeUpdate, PromotionStatusUpdate,
)


def _user():
    return fx.make_user_double(name="Admin de prueba")


def _create(db, **kw):
    body = PromotionCreate(**kw)
    return promo_router.create_promotion(body=body, db=db, user=_user())


class TestPresentationRulesUS1(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.p8 = fx.make_presentation(self.db, name="8oz")
        self.p16 = fx.make_presentation(self.db, name="16oz")
        prod_a = fx.make_product(self.db, name="Ojo de Diablo")
        prod_b = fx.make_product(self.db, name="Fresa Boom")
        self.v8a = fx.make_variant(self.db, product=prod_a, name="8oz", price="7000")
        self.v8b = fx.make_variant(self.db, product=prod_b, name="8oz", price="7000")
        fx.assign_presentation(self.db, self.v8a, self.p8)
        fx.assign_presentation(self.db, self.v8b, self.p8)
        self.db.commit()

    def test_crea_con_dos_reglas_y_expone_alcance(self):
        """CA-1 / FR-005: dos reglas, cada una con `applicable_variant_count`."""
        promo = _create(
            self.db, name="Promo presentación", type="qty_price_presentation", value=0,
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
                {"presentation_id": str(self.p16.id), "min_qty": 2, "pack_price": "16500"},
            ],
        )
        rules = {r["presentation_name"]: r for r in promo["presentation_rules"]}
        self.assertEqual(set(rules), {"8oz", "16oz"})
        self.assertEqual(rules["8oz"]["applicable_variant_count"], 2)
        self.assertEqual(rules["16oz"]["applicable_variant_count"], 0)

    def test_segunda_regla_misma_presentacion_rechazada(self):
        """CA-2 / FR-006 (1ª parte): no dos reglas para la misma presentación."""
        with self.assertRaises(Exception) as ctx:
            _create(
                self.db, name="Duplicada", type="qty_price_presentation", value=0,
                presentation_rules=[
                    {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
                    {"presentation_id": str(self.p8.id), "min_qty": 3, "pack_price": "18000"},
                ],
            )
        # Pydantic ValidationError o HTTPException 422 — cualquiera bloquea.
        self.assertNotIsInstance(ctx.exception, type(None))

    def test_solape_entre_promociones_activas_bloquea(self):
        """CA-3 / CL-4 / FR-006 (2ª parte): 409 nombrando el conflicto, tanto al
        crear como al activar desde `draft`."""
        activa = _create(
            self.db, name="Activa 8oz", type="qty_price_presentation", value=0,
            status="active",
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
            ],
        )
        # crear otra con regla sobre 8oz -> 409
        with self.assertRaises(HTTPException) as ctx:
            _create(
                self.db, name="Otra 8oz", type="qty_price_presentation", value=0,
                presentation_rules=[
                    {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "10000"},
                ],
            )
        self.assertEqual(ctx.exception.status_code, 409)
        conflicts = ctx.exception.detail["conflicts"]
        self.assertEqual(conflicts[0]["promotion_name"], "Activa 8oz")
        self.assertEqual(conflicts[0]["promotion_id"], str(activa["id"]))

        # una promoción creada en `draft` SIN conflicto (16oz), a la que luego se
        # le agrega la regla de 8oz mientras "Activa 8oz" sigue activa: el shape
        # que introduce el solape ya bloquea (FR-006, "al guardar o activar").
        draft = _create(
            self.db, name="Draft 16oz", type="qty_price_presentation", value=0,
            presentation_rules=[
                {"presentation_id": str(self.p16.id), "min_qty": 2, "pack_price": "16500"},
            ],
        )
        with self.assertRaises(HTTPException) as ctx2:
            promo_router.update_promotion_shape(
                promotion_id=draft["id"],
                body=PromotionShapeUpdate(presentation_rules=[
                    {"presentation_id": str(self.p16.id), "min_qty": 2, "pack_price": "16500"},
                    {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "9000"},
                ]),
                db=self.db, user=_user(),
            )
        self.assertEqual(ctx2.exception.status_code, 409)

    def test_solape_revalidado_al_activar_un_draft_preexistente(self):
        """research.md D8: un `draft` creado sin conflicto que, al activarse,
        choca con otra promoción que se activó entretanto -> 409 en `change_status`."""
        draft = _create(
            self.db, name="Draft 8oz", type="qty_price_presentation", value=0,
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
            ],
        )
        # otra promoción toma 8oz y se activa (el draft no la bloquea: es draft)
        _create(
            self.db, name="Se adelantó", type="qty_price_presentation", value=0,
            status="active",
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "11000"},
            ],
        )
        with self.assertRaises(HTTPException) as ctx:
            promo_router.change_promotion_status(
                promotion_id=draft["id"],
                body=PromotionStatusUpdate(status="active"), db=self.db, user=_user(),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["conflicts"][0]["promotion_name"], "Se adelantó")

    def test_ventana_cruza_medianoche_aceptada(self):
        """CA-4 / FR-003: 22:00-02:00 se guarda como ventana válida."""
        promo = _create(
            self.db, name="Nocturna", type="qty_price_presentation", value=0,
            start_time=time(22, 0), end_time=time(2, 0), days_of_week="0",
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
            ],
        )
        self.assertEqual(promo["start_time"], time(22, 0))
        self.assertEqual(promo["end_time"], time(2, 0))

    def test_atribucion_de_dia_al_cruzar_medianoche(self):
        """CA (FR-004 / CL-8 / A-55): una promo `qty_price_presentation` activa con
        ventana 22:00-02:00 los lunes está vigente el lunes 23:00 y el martes
        01:00, y NO el martes 03:00 ni el miércoles 01:00."""
        _create(
            self.db, name="Nocturna activa", type="qty_price_presentation", value=0,
            status="active",
            start_time=time(22, 0), end_time=time(2, 0), days_of_week="0",  # lunes
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
            ],
        )
        promo = self.db.query(promo_service.Promotion).filter_by(name="Nocturna activa").one()
        # Lunes 2026-08-03. Bogotá = UTC-5.
        lunes_23 = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)   # lun 23:00 local
        martes_01 = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)  # mar 01:00 local
        martes_03 = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)  # mar 03:00 local
        miercoles_01 = datetime(2026, 8, 5, 6, 0, tzinfo=timezone.utc)
        self.assertTrue(promo_service._valid_now(promo, lunes_23))
        self.assertTrue(promo_service._valid_now(promo, martes_01))
        self.assertFalse(promo_service._valid_now(promo, martes_03))
        self.assertFalse(promo_service._valid_now(promo, miercoles_01))

    def test_reglas_prohibidas_para_otro_tipo(self):
        """research.md D4: `presentation_rules` con otro `type` -> 422 (Pydantic)."""
        with self.assertRaises(Exception):
            _create(
                self.db, name="Mal tipo", type="percent", value=10,
                presentation_rules=[
                    {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
                ],
            )

    def test_duplicate_copia_las_reglas(self):
        """§5: `duplicate` copia `presentation_rules` (filas nuevas, sin compartir id)."""
        from app.api.v1.promotions.schemas import PromotionDuplicate
        promo = _create(
            self.db, name="Original", type="qty_price_presentation", value=0,
            presentation_rules=[
                {"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"},
            ],
        )
        copy = promo_router.duplicate_promotion(
            promotion_id=promo["id"], body=PromotionDuplicate(name="Copia"),
            db=self.db, user=_user(),
        )
        self.assertEqual(copy["status"], "draft")
        self.assertEqual(len(copy["presentation_rules"]), 1)
        self.assertEqual(copy["presentation_rules"][0]["presentation_name"], "8oz")


class TestPresentationRulesUS3(unittest.TestCase):
    """US3 — avisos de precio no uniforme (FR-017) y de "no es descuento real"
    (FR-022), con confirmación explícita; nunca retroactivos (FR-018)."""

    def setUp(self):
        self.db = fx.new_session()
        self.p8 = fx.make_presentation(self.db, name="8oz")
        prod_a = fx.make_product(self.db, name="Ojo de Diablo")
        prod_b = fx.make_product(self.db, name="Fresa Boom")
        self.v_barata = fx.make_variant(self.db, product=prod_a, name="8oz", price="7000")
        self.v_cara = fx.make_variant(self.db, product=prod_b, name="8oz", price="8000")
        fx.assign_presentation(self.db, self.v_barata, self.p8)
        fx.assign_presentation(self.db, self.v_cara, self.p8)
        self.db.commit()

    def test_precio_no_uniforme_bloquea_sin_flag_y_guarda_con_flag(self):
        rules = [{"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"}]
        with self.assertRaises(HTTPException) as ctx:
            _create(self.db, name="No uniforme", type="qty_price_presentation", value=0,
                    presentation_rules=rules)
        self.assertEqual(ctx.exception.status_code, 422)
        detalle = ctx.exception.detail
        self.assertEqual(detalle["reference_unit_price"], "7000.00")
        self.assertEqual(len(detalle["variants"]), 2)
        self.db.rollback()  # lo que hace `get_db` tras un error del endpoint

        promo = _create(self.db, name="No uniforme", type="qty_price_presentation", value=0,
                        presentation_rules=rules, confirm_precio_no_uniforme=True)
        self.assertEqual(len(promo["presentation_rules"]), 1)

    def test_no_revalida_retroactivamente_cambio_de_precio(self):
        """FR-018 / CL-1: una regla guardada con el flag no se revalida si un
        producto cambia de precio después."""
        promo = _create(
            self.db, name="Activa", type="qty_price_presentation", value=0, status="active",
            presentation_rules=[{"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"}],
            confirm_precio_no_uniforme=True,
        )
        # el producto caro sube más — no hay job ni trigger que revise nada
        self.v_cara.price = Decimal("20000")
        self.db.commit()
        reloaded = self.db.query(promo_service.Promotion).filter_by(name="Activa").one()
        self.assertEqual(len(reloaded.presentation_rules), 1)

    def test_variante_nueva_no_pasa_por_la_verificacion(self):
        """CL-1b: una variante nueva asignada a la presentación de una regla
        activa entra sin re-chequeo de uniformidad."""
        _create(
            self.db, name="Activa2", type="qty_price_presentation", value=0, status="active",
            presentation_rules=[{"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "12000"}],
            confirm_precio_no_uniforme=True,
        )
        prod_c = fx.make_product(self.db, name="Maracumango")
        v_nueva = fx.make_variant(self.db, product=prod_c, name="8oz", price="99000")
        fx.assign_presentation(self.db, v_nueva, self.p8)
        self.db.commit()  # sin excepción: no hay verificación al asignar

    def test_fr022_regla_sin_descuento_real(self):
        """FR-022: `pack_price / min_qty >= reference_unit_price` -> 422 sin flag,
        se guarda con `confirm_sin_descuento`."""
        # presentación con precio uniforme para aislar FR-022 de FR-017
        self.v_cara.price = Decimal("7000")
        self.db.commit()
        rules = [{"presentation_id": str(self.p8.id), "min_qty": 2, "pack_price": "14000"}]
        with self.assertRaises(HTTPException) as ctx:
            _create(self.db, name="Sin descuento", type="qty_price_presentation", value=0,
                    presentation_rules=rules)
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(ctx.exception.detail["pack_unit_price"], "7000.00")
        self.db.rollback()

        promo = _create(self.db, name="Sin descuento", type="qty_price_presentation", value=0,
                        presentation_rules=rules, confirm_sin_descuento=True)
        self.assertEqual(len(promo["presentation_rules"]), 1)


if __name__ == "__main__":
    unittest.main()
