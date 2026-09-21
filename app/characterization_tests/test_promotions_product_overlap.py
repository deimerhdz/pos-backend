"""Tests de la nueva funcionalidad — spec 084-fix-promociones-productos,
US5: exclusividad de producto entre promociones `active` (FR-021 a FR-025).
Cita anomalía A-78 (registro-de-anomalias.md).

Complementa, sin reemplazar, `test_promotions_rules_admin.py::TestUS3SolapeReal`
(`_guard_variant_overlap`, spec 063 FR-014, sin cambio).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_product_overlap -v
"""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException

from app.characterization_tests import cart_fixtures as fx
from app.api.v1.promotions import service
from app.api.v1.promotions.schemas import PromotionCreate, PromotionShapeUpdate

STARTS = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def _rule(**kw):
    kw.setdefault("type", "percent")
    kw.setdefault("value", Decimal("10"))
    kw.setdefault("min_qty", 1)
    kw.setdefault("variant_ids", [])
    return kw


def _create_payload(**kw):
    kw.setdefault("name", "promo")
    kw.setdefault("starts_at", STARTS)
    if "rules" not in kw:
        rule_kw = {}
        for k in ("type", "value", "min_qty", "variant_ids"):
            if k in kw:
                rule_kw[k] = kw.pop(k)
        kw["rules"] = [_rule(**rule_kw)]
    return PromotionCreate(**kw)


class GuardProductOverlapTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.producto = fx.make_product(self.db)
        self.grande = fx.make_variant(self.db, product=self.producto, price=Decimal("10000"), name="Grande")
        self.pequena = fx.make_variant(self.db, product=self.producto, price=Decimal("8000"), name="Pequeña")
        self.otro_producto = fx.make_product(self.db)
        self.otra_variante = fx.make_variant(self.db, product=self.otro_producto, price=Decimal("6000"), name="Única")
        self.db.commit()

    def _activar(self, name, variant_ids):
        p = service.create(self.db, _create_payload(name=name, variant_ids=variant_ids))
        service.change_status(self.db, p, "active")
        self.db.commit()
        return p

    def test_producto_completo_bloquea_aunque_la_variante_sea_distinta(self):
        """FR-021: 'Grande' ya cubierta por una promoción activa -> ninguna variante
        de ese mismo producto (ni siquiera 'Pequeña', distinta) puede entrar a otra."""
        self._activar("activa", [self.grande.id])

        with self.assertRaises(HTTPException) as ctx:
            self._activar("nueva", [self.pequena.id])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("activa", ctx.exception.detail)

    def test_misma_variante_tambien_bloquea(self):
        """Caso más simple: la MISMA variante -- ya lo bloqueaba FR-014, y ahora
        también FR-021 (ambas guardas coinciden en este caso)."""
        self._activar("activa", [self.grande.id])

        with self.assertRaises(HTTPException) as ctx:
            self._activar("nueva", [self.grande.id])
        self.assertEqual(ctx.exception.status_code, 409)

    def test_producto_distinto_no_bloquea(self):
        """Un producto sin ninguna relación con la promoción activa sigue disponible."""
        self._activar("activa", [self.grande.id])

        nueva = self._activar("nueva", [self.otra_variante.id])
        self.assertEqual(nueva.status, "active")

    def test_fr022_mensaje_explica_que_promocion_ya_cubre_el_producto(self):
        self._activar("Promo de invierno", [self.grande.id])

        with self.assertRaises(HTTPException) as ctx:
            self._activar("nueva", [self.pequena.id])
        self.assertIn("Promo de invierno", ctx.exception.detail)
        self.assertIn(self.producto.name, ctx.exception.detail)

    def test_fr024_dentro_de_la_misma_promocion_varias_variantes_del_mismo_producto_estan_permitidas(self):
        """Seleccionar 'Grande' y 'Pequeña' del mismo producto en la MISMA
        promoción (dos reglas) no debe chocar consigo misma."""
        p = service.create(self.db, PromotionCreate(
            name="ambas", starts_at=STARTS, rules=[
                _rule(variant_ids=[self.grande.id]),
                _rule(variant_ids=[self.pequena.id]),
            ],
        ))
        activada = service.change_status(self.db, p, "active")
        self.assertEqual(activada.status, "active")

    def test_fr023_condicion_de_carrera_al_activar_la_segunda_rechaza(self):
        """Dos promociones en `draft`, ambas sobre el mismo producto, se pueden
        crear sin chocar (ninguna es `active` todavía) -- pero activar la
        segunda después de la primera sí choca (FR-023)."""
        a = service.create(self.db, _create_payload(name="a", variant_ids=[self.grande.id], status="draft"))
        b = service.create(self.db, _create_payload(name="b", variant_ids=[self.pequena.id], status="draft"))
        self.db.commit()

        service.change_status(self.db, a, "active")
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            service.change_status(self.db, b, "active")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_update_shape_de_una_promocion_en_borrador_tambien_queda_sujeto_a_la_guarda(self):
        """FR-021: armar el Paso 1/Paso 2 de una promoción nueva en `draft` ya
        queda sujeto a la exclusividad, no solo la activación."""
        self._activar("activa", [self.grande.id])
        borrador = service.create(self.db, _create_payload(
            name="borrador", variant_ids=[self.otra_variante.id], status="draft",
        ))
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            service.update_shape(self.db, borrador, PromotionShapeUpdate(rules=[
                _rule(variant_ids=[self.pequena.id]),
            ]))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_fr025_no_retroactivo_promociones_ya_activas_con_el_criterio_anterior_no_se_tocan(self):
        """No hay ninguna tarea de fondo que re-evalúe promociones ya `active` --
        esta guarda solo corre en las rutas de escritura (create/update_shape/
        change_status), nunca de forma pasiva sobre datos ya persistidos."""
        a = self._activar("a", [self.grande.id])
        # Si existiera una promoción "b" activa desde ANTES de esta guarda que
        # ya compartiera producto con "a" (posible bajo el criterio anterior,
        # por variante), nada en este flujo la toca ni la revalida sin que
        # alguien la edite o la reactive explícitamente -- lo único que
        # confirma este test, en ausencia de una tarea batch, es que "a"
        # sigue activa y utilizable sin que esta guarda la re-evalúe sola.
        self.db.refresh(a)
        self.assertEqual(a.status, "active")


if __name__ == "__main__":
    unittest.main()
