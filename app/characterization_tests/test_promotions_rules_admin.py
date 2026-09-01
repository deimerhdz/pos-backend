"""Tests de la nueva funcionalidad — spec 063-promociones-por-variante,
US1 (armar la promoción con una o varias reglas), US3 (solape real) y US5
(duplicar, editar activa, estados, mantenimiento por lote).
`contracts/administracion-promociones.md`.

Decisión de negocio: A-58…A-65 (registro-de-anomalias.md, 2026-08-31,
propietario del repositorio). Modelo `Promoción`/`Regla`: Clarifications de
`spec.md`, sesión 2026-09-01.

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


def _rule(**kw):
    kw.setdefault("type", "package_price")
    kw.setdefault("value", Decimal("12000"))
    kw.setdefault("min_qty", 2)
    kw.setdefault("variant_ids", [])
    return kw


def _create_payload(**kw):
    """spec 063 (revisión 2026-09-01): si no se pasa `rules` explícito, arma
    una promoción de **una sola** regla a partir de los kwargs de tipo/valor/
    cantidad mínima/conjunto sueltos — mismo estilo de llamada que los tests
    del modelo plano, para no reescribir cada caso desde cero."""
    kw.setdefault("name", "promo")
    kw.setdefault("starts_at", STARTS)
    if "rules" not in kw:
        rule_kw = {}
        for k in ("type", "value", "min_qty", "variant_ids"):
            if k in kw:
                rule_kw[k] = kw.pop(k)
        kw["rules"] = [_rule(**rule_kw)]
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

    def test_ca4_conjunto_vacio_de_una_regla_rechazado(self):
        with self.assertRaises(ValidationError):
            _create_payload(variant_ids=[])

    def test_ca5_percent_mayor_a_100_rechazado(self):
        with self.assertRaises(ValidationError):
            _create_payload(type="percent", value=Decimal("150"), min_qty=1,
                            variant_ids=self._ids(1))

    def test_ca1_ca6_paquete_nace_borrador_con_condicion(self):
        promo = service.create(
            self.db,
            _create_payload(name="2X Pequeños con licor $12.000", variant_ids=self._ids()),
        )
        self.db.commit()
        data = service.serialize_promotion(self.db, promo)
        self.assertEqual(data["status"], "draft")
        regla = data["rules"][0]
        self.assertEqual(regla["condition_text"],
                         "Llevando 2 de estas 8 variantes pagas $12.000")
        self.assertEqual(len(regla["variants"]), 8)
        self.assertTrue(all(v["unit_price"] == Decimal("8000") for v in regla["variants"]))

    def test_ca3_una_variante_creada_despues_no_entra_sola(self):
        promo = service.create(self.db, _create_payload(variant_ids=self._ids()))
        self.db.commit()
        # una variante nueva en el mismo producto, DESPUÉS de guardar
        nueva = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"), name="nueva")
        self.db.commit()
        data = service.serialize_promotion(self.db, promo)
        ids = {v["product_variant_id"] for v in data["rules"][0]["variants"]}
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
        self.assertIn("rule_id", ctx.exception.detail)

    # ---- FR-001: una promoción necesita al menos una regla ----
    def test_fr001_promocion_sin_reglas_rechazada(self):
        with self.assertRaises(ValidationError):
            PromotionCreate(name="sin reglas", starts_at=STARTS, rules=[])

    # ---- FR-001a: variante repetida entre dos reglas del MISMO payload ----
    def test_fr001a_variante_repetida_entre_reglas_de_la_misma_promocion_bloquea(self):
        compartida = self.variantes[0]
        with self.assertRaises(HTTPException) as ctx:
            service.create(self.db, _create_payload(
                rules=[
                    _rule(type="percent", value=Decimal("10"), min_qty=1,
                          variant_ids=[compartida.id, self.variantes[1].id]),
                    _rule(type="package_price", value=Decimal("12000"), min_qty=2,
                          variant_ids=[compartida.id, self.variantes[2].id]),
                ],
            ))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn(str(compartida.id), ctx.exception.detail["variant_ids"])
        self.assertEqual(ctx.exception.detail["rule_index_a"], 0)
        self.assertEqual(ctx.exception.detail["rule_index_b"], 1)

    # ---- Creación por lote: caso Springfield, 6 reglas en una sola llamada ----
    def test_creacion_por_lote_seis_reglas_en_una_sola_promocion(self):
        prod = fx.make_product(self.db)
        precios = [
            ("Pequeños", Decimal("8000"), Decimal("12000")),
            ("Medianos", Decimal("11000"), Decimal("17000")),
            ("Grandes", Decimal("15000"), Decimal("22000")),
            ("Extra grandes", Decimal("18000"), Decimal("27000")),
            ("Baldes", Decimal("21000"), Decimal("31000")),
            ("Litros", Decimal("28000"), Decimal("41000")),
        ]
        rules = []
        for nombre, unit_price, paquete in precios:
            v1 = fx.make_variant(self.db, product=prod, price=unit_price, name=f"{nombre}-a")
            v2 = fx.make_variant(self.db, product=prod, price=unit_price, name=f"{nombre}-b")
            rules.append(_rule(
                type="package_price", value=paquete, min_qty=2, variant_ids=[v1.id, v2.id],
            ))
        self.db.commit()

        promo = service.create(self.db, _create_payload(
            name="2X entre semana", rules=rules, days_of_week="0,1,2,3",
        ))
        self.db.commit()

        data = service.serialize_promotion(self.db, promo)
        self.assertEqual(data["status"], "draft")
        self.assertEqual(len(data["rules"]), 6)
        for regla in data["rules"]:
            self.assertEqual(len(regla["variants"]), 2)


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
        self.assertIn("rule_id", conflicto)

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

    def test_ca2_cambiar_reglas_de_una_activa_bloquea(self):
        """spec 063 (revisión 2026-09-01, FR-018): `PromotionUpdate` ya no
        tiene `value`/`min_qty`/conjunto — el único camino para tocar una
        regla es `update_shape`, y ahí se bloquea fuera de `draft`."""
        with self.assertRaises(HTTPException) as ctx:
            service.update_shape(self.db, self.activa, PromotionShapeUpdate(rules=[_rule(
                type="percent", value=Decimal("20"), min_qty=1, variant_ids=[self.v2.id],
            )]))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_ca3_reactivar_finalizada_409(self):
        service.change_status(self.db, self.activa, "finished")
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx:
            service.change_status(self.db, self.activa, "active")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_ca4_duplicar_copia_borrador_con_las_mismas_reglas(self):
        copia = service.duplicate(self.db, self.activa, "activa (copia)")
        self.db.commit()
        self.assertEqual(copia.status, "draft")
        self.assertEqual(len(copia.rules), len(self.activa.rules))
        self.assertEqual(copia.rules[0].type, self.activa.rules[0].type)
        self.assertEqual(copia.rules[0].value, self.activa.rules[0].value)
        self.assertEqual(
            {pv.product_variant_id for pv in copia.rules[0].variants},
            {pv.product_variant_id for pv in self.activa.rules[0].variants},
        )
        # Cambiar el valor y el conjunto de una regla en la copia (draft) sí
        # se permite — con `self.v2` (no `self.v`, que sigue en uso por
        # `self.activa`, todavía `Activa`: reusar la misma variante sí
        # dispararía FR-014, correctamente, porque ambas promociones
        # coexistirían sobre ella sin ninguna ventana que las separe).
        service.update_shape(self.db, copia, PromotionShapeUpdate(rules=[_rule(
            type="percent", value=Decimal("15"), min_qty=1,
            variant_ids=[self.v2.id],
        )]))
        self.db.commit()
        self.assertEqual(copia.rules[0].value, Decimal("15"))

    def test_ca6_cajero_no_puede_gestionar_promociones(self):
        """FR-019: solo el administrador del tenant gestiona promociones. El
        bloqueo de permisos en sí vive en `require_tenant_admin`
        (`core/dependencies.py`) — probado a nivel de router en
        `test_promotions_router.py`; aquí solo se deja constancia de que
        `service.*` no vuelve a chequear el rol (responsabilidad de la capa
        de router, sin cambio en esta revisión)."""
        self.assertTrue(True)


class TestUS5MantenimientoPorLote(unittest.TestCase):
    """spec 063 (revisión 2026-09-01): pausar, activar o extender la vigencia
    de una promoción con varias reglas es **una sola acción** que afecta a
    todas — el caso de uso central que motivó la partición Promoción/Regla
    (Clarifications, sesión 2026-09-01)."""

    def setUp(self):
        self.db = fx.new_session()
        self.prod = fx.make_product(self.db)
        rules = []
        for i in range(6):
            v1 = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"), name=f"r{i}a")
            v2 = fx.make_variant(self.db, product=self.prod, price=Decimal("8000"), name=f"r{i}b")
            rules.append(_rule(
                type="package_price", value=Decimal("12000"), min_qty=2,
                variant_ids=[v1.id, v2.id],
            ))
        self.db.commit()
        self.promo = service.create(self.db, _create_payload(
            name="2X entre semana", rules=rules,
        ))
        service.change_status(self.db, self.promo, "active")
        self.db.commit()

    def test_editar_vigencia_de_promocion_multi_regla_afecta_a_todas_con_una_accion(self):
        nueva_fecha = datetime(2026, 12, 31, tzinfo=timezone.utc)
        service.update(self.db, self.promo, PromotionUpdate(ends_at=nueva_fecha))
        self.db.commit()
        # SQLite no conserva tzinfo en un DateTime — se compara naive.
        self.assertEqual(self.promo.ends_at, nueva_fecha.replace(tzinfo=None))
        # las 6 reglas siguen colgando de la misma promoción — no hicieron
        # falta 6 llamadas para que la vigencia nueva les aplique a todas.
        self.assertEqual(len(self.promo.rules), 6)

    def test_pausar_promocion_de_seis_reglas_con_una_sola_llamada_deja_las_seis_sin_efecto(self):
        now = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)  # miércoles, vigente
        antes = service.active_variant_set_rules(self.db, now)
        self.assertEqual(len(antes), 6)

        service.change_status(self.db, self.promo, "paused")
        self.db.commit()

        despues = service.active_variant_set_rules(self.db, now)
        self.assertEqual(despues, [])
        self.assertTrue(all(r.promotion_id == self.promo.id for r in self.promo.rules))


if __name__ == "__main__":
    unittest.main()
