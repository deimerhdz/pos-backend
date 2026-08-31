"""spec 040 — US4 (parte): catálogo de presentaciones y baja bloqueada por
regla activa (FR-020 / CL-2), y entrada automática de variantes nuevas por
referencia a la presentación (FR-007 / FR-019 / CA-9).

Invoca el router de presentaciones directamente como funciones Python.

    python -m unittest app.characterization_tests.test_presentations_service -v
"""
import unittest

from fastapi import HTTPException

from app.characterization_tests import presentation_fixtures as fx
from app.api.v1.presentations import router as pres_router
from app.api.v1.presentations import service as pres_service
from app.api.v1.presentations.schemas import PresentationCreate, PresentationUpdate
from app.models.presentation import Presentation


def _user():
    return fx.make_user_double(name="Admin")


def _list(db, **q):
    return pres_router.list_presentations(
        page=q.get("page", 1), size=q.get("size", 20),
        active=q.get("active"), search=q.get("search"), db=db, _=_user(),
    )


class TestPresentationsCatalogUS4(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def _count_for(self, presentation_id):
        return pres_service.applicable_variant_counts(self.db, [presentation_id]).get(
            presentation_id, 0
        )

    def test_unicidad_de_nombre(self):
        pres_router.create_presentation(
            body=PresentationCreate(name="8oz"), db=self.db, _=_user()
        )
        with self.assertRaises(HTTPException) as ctx:
            pres_router.create_presentation(
                body=PresentationCreate(name="8oz"), db=self.db, _=_user()
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_alcance_por_referencia_y_variante_nueva_entra_sola(self):
        """FR-007 / FR-019 / CA-9: el conteo de aplicables se resuelve por
        `presentation_id`; una variante creada después la incluye sin tocar nada."""
        p8 = fx.make_presentation(self.db, name="8oz")
        prod_a = fx.make_product(self.db, name="Ojo de Diablo")
        prod_b = fx.make_product(self.db, name="Fresa Boom")
        va = fx.make_variant(self.db, product=prod_a, name="8oz", price="7000")
        vb = fx.make_variant(self.db, product=prod_b, name="8oz", price="7000")
        fx.assign_presentation(self.db, va, p8)
        fx.assign_presentation(self.db, vb, p8)
        self.db.commit()
        self.assertEqual(self._count_for(p8.id), 2)

        prod_c = fx.make_product(self.db, name="Maracumango")
        vc = fx.make_variant(self.db, product=prod_c, name="8oz", price="7000")
        fx.assign_presentation(self.db, vc, p8)
        self.db.commit()
        self.assertEqual(self._count_for(p8.id), 3)

    def test_baja_bloqueada_por_regla_de_promocion_activa(self):
        """FR-020 / CL-2: DELETE y PATCH active=false devuelven 409 con la lista
        de promociones mientras una promoción `active` referencie la presentación;
        al pausarla, la baja procede y las variantes quedan con `presentation_id NULL`."""
        p8 = fx.make_presentation(self.db, name="8oz")
        prod = fx.make_product(self.db, name="Ojo de Diablo")
        v = fx.make_variant(self.db, product=prod, name="8oz", price="7000")
        fx.assign_presentation(self.db, v, p8)
        promo = fx.make_promotion(
            self.db, name="Precio 8oz", type="qty_price_presentation",
            status="active", value=0,
        )
        fx.make_presentation_rule(self.db, promo, p8, min_qty=2, pack_price="12000")
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx_del:
            pres_router.delete_presentation(presentation_id=p8.id, db=self.db, _=_user())
        self.assertEqual(ctx_del.exception.status_code, 409)
        self.assertEqual(
            ctx_del.exception.detail["promotions"][0]["name"], "Precio 8oz"
        )
        self.db.rollback()

        with self.assertRaises(HTTPException) as ctx_patch:
            pres_router.update_presentation(
                presentation_id=p8.id, body=PresentationUpdate(active=False),
                db=self.db, _=_user(),
            )
        self.assertEqual(ctx_patch.exception.status_code, 409)
        self.db.rollback()

        # pausar la promoción -> la baja procede
        promo.status = "paused"
        self.db.commit()
        pres_router.delete_presentation(presentation_id=p8.id, db=self.db, _=_user())
        self.assertIsNone(self.db.get(Presentation, p8.id))
        # (el `ON DELETE SET NULL` sobre `product_variants.presentation_id` lo
        # verifica T005 contra PostgreSQL real; SQLite no fuerza FKs por defecto.)

    def test_renombrar_no_esta_bloqueado_por_uso(self):
        """El alcance se resuelve por `id`, así que renombrar una presentación en
        uso por una promoción activa NO se bloquea (solo baja/borrado)."""
        p8 = fx.make_presentation(self.db, name="8oz")
        promo = fx.make_promotion(
            self.db, name="Precio 8oz", type="qty_price_presentation",
            status="active", value=0,
        )
        fx.make_presentation_rule(self.db, promo, p8, min_qty=2, pack_price="12000")
        self.db.commit()

        out = pres_router.update_presentation(
            presentation_id=p8.id, body=PresentationUpdate(name="8 onzas"),
            db=self.db, _=_user(),
        )
        self.assertEqual(out["name"], "8 onzas")


if __name__ == "__main__":
    unittest.main()
