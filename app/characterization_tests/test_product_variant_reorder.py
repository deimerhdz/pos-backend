"""Cubre `app/api/v1/catalog/service.py` (`_next_display_order`) y
`app/api/v1/catalog/router.py` (`list_variants`) -- funcionalidad NUEVA (spec 042, no
CONGELA nada existente).

`ReorderVariantsTests` (la clase que probaba `reorder_variants`/`VariantReorderError`/
`reorder_product_variants` directamente) se retiró en spec 043: ese endpoint dedicado de
reordenamiento se retiró (A-55, registro-de-anomalias.md) porque el guardado consolidado de
`POST`/`PATCH /products` asigna `display_order` según la posición de cada presentación en
`variants[]` (`_assign_display_orders`, `catalog/service.py`) -- cubierto por
`test_products_service.py` (`TestCreateProductWithVariantTree`/`TestUpdateProductWithVariantTree`).
Las clases restantes de este archivo siguen vigentes: ejercitan `_next_display_order`,
`ensure_default_variant`, `create_variant`/`update_variant`/`delete_variant` (spec 042), ninguna
de las cuales se retiró.

Referencias: `specs/042-orden-presentaciones-producto/spec.md` (FR-001 a FR-010),
`data-model.md` (tabla de asignación), `research.md` (Decisiones 2 a 5).
"""
import unittest

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.service import _next_display_order, ensure_default_variant
from app.api.v1.catalog.router import create_variant, delete_variant, list_variants, update_variant
from app.api.v1.catalog.schemas import VariantCreate, VariantUpdate


class NextDisplayOrderTests(unittest.TestCase):
    """FR-005/FR-009: una presentación nueva se agrega al final."""

    def setUp(self):
        self.db = f.new_session()

    def test_primera_presentacion_del_producto_es_1(self):
        product = f.make_product(self.db)
        self.assertEqual(_next_display_order(self.db, product.id), 1)

    def test_siguiente_presentacion_es_max_mas_1(self):
        product = f.make_product(self.db)
        f.make_variant(self.db, product, display_order=1)
        f.make_variant(self.db, product, display_order=5)
        self.assertEqual(_next_display_order(self.db, product.id), 6)

    def test_cuenta_tambien_las_desactivadas(self):
        """FR-005: agregar no debe alterar el orden existente -- incluida una
        presentación ya desactivada, que sigue ocupando su posición."""
        product = f.make_product(self.db)
        f.make_variant(self.db, product, display_order=1)
        desactivada = f.make_variant(self.db, product, display_order=2, active=False)
        self.assertEqual(_next_display_order(self.db, product.id), 3)


class EnsureDefaultVariantOrderTests(unittest.TestCase):
    """FR-009: el primer 'Single' de un producto nuevo nace en la posición 1."""

    def setUp(self):
        self.db = f.new_session()

    def test_single_nace_con_display_order_1(self):
        product = f.make_product(self.db)
        variant = ensure_default_variant(self.db, product)
        self.assertEqual(variant.display_order, 1)


class CreateVariantOrderTests(unittest.TestCase):
    """FR-005: crear una presentación nueva no altera el orden de las existentes."""

    def setUp(self):
        self.db = f.new_session()

    def test_nueva_presentacion_se_agrega_al_final(self):
        product = f.make_product(self.db)
        v1 = f.make_variant(self.db, product, name="Pequeña", display_order=1)
        v2 = f.make_variant(self.db, product, name="Grande", display_order=2)

        nueva = create_variant(
            product.id, VariantCreate(name="Mediana", price=0), self.db, None
        )

        self.assertEqual(nueva.display_order, 3)
        self.db.refresh(v1)
        self.db.refresh(v2)
        self.assertEqual(v1.display_order, 1)
        self.assertEqual(v2.display_order, 2)


class DeleteReactivateOrderTests(unittest.TestCase):
    """FR-006 a FR-008: editar/eliminar/reactivar no altera `display_order`."""

    def setUp(self):
        self.db = f.new_session()

    def test_editar_nombre_o_precio_no_cambia_el_orden(self):
        product = f.make_product(self.db)
        variant = f.make_variant(self.db, product, name="Pequeña", display_order=1)
        update_variant(variant.id, VariantUpdate(name="Chica", price=5000), self.db, None)
        self.db.refresh(variant)
        self.assertEqual(variant.display_order, 1)
        self.assertEqual(variant.name, "Chica")

    def test_eliminar_no_deja_huecos_ni_toca_las_demas(self):
        product = f.make_product(self.db)
        v1 = f.make_variant(self.db, product, name="Pequeña", display_order=1)
        v2 = f.make_variant(self.db, product, name="Mediana", display_order=2)
        v3 = f.make_variant(self.db, product, name="Grande", display_order=3)

        delete_variant(v2.id, self.db, None)

        self.db.refresh(v1)
        self.db.refresh(v2)
        self.db.refresh(v3)
        self.assertFalse(v2.active)
        # research.md Decisión 4: display_order NO se recalcula al desactivar --
        # v1 y v3 conservan exactamente su posición, y v2 conserva la suya para
        # cuando se reactive.
        self.assertEqual(v1.display_order, 1)
        self.assertEqual(v2.display_order, 2)
        self.assertEqual(v3.display_order, 3)

    def test_reactivar_conserva_el_orden_que_tenia_antes_de_desactivarse(self):
        product = f.make_product(self.db)
        v1 = f.make_variant(self.db, product, name="Pequeña", display_order=1)
        v2 = f.make_variant(self.db, product, name="Mediana", display_order=2)
        v3 = f.make_variant(self.db, product, name="Grande", display_order=3)

        delete_variant(v2.id, self.db, None)
        update_variant(v2.id, VariantUpdate(active=True), self.db, None)

        self.db.refresh(v2)
        self.assertTrue(v2.active)
        self.assertEqual(v2.display_order, 2)

        listado = list_variants(product.id, True, self.db, None)
        self.assertEqual([v.id for v in listado], [v1.id, v2.id, v3.id])
