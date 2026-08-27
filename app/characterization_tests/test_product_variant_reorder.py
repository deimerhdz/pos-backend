"""Cubre `app/api/v1/catalog/service.py` (`reorder_variants`, `_next_display_order`) y
`app/api/v1/catalog/router.py` (`list_variants`, `reorder_product_variants`) --
funcionalidad NUEVA (spec 042, no CONGELA nada existente).

Referencias: `specs/042-orden-presentaciones-producto/spec.md` (FR-001 a FR-010),
`data-model.md` (tabla de asignación), `research.md` (Decisiones 2 a 5).
"""
import unittest
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.service import (
    _next_display_order,
    ensure_default_variant,
    reorder_variants,
    VariantReorderError,
)
from app.api.v1.catalog.router import (
    create_variant,
    delete_variant,
    list_variants,
    reorder_product_variants,
    update_variant,
)
from app.api.v1.catalog.schemas import VariantCreate, VariantReorderRequest, VariantUpdate


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


class ReorderVariantsTests(unittest.TestCase):
    """FR-001 a FR-003, FR-010: el endpoint/función que reordena."""

    def setUp(self):
        self.db = f.new_session()

    def _producto_con_tres_presentaciones(self):
        product = f.make_product(self.db)
        v1 = f.make_variant(self.db, product, name="Pequeña", display_order=1)
        v2 = f.make_variant(self.db, product, name="Mediana", display_order=2)
        v3 = f.make_variant(self.db, product, name="Grande", display_order=3)
        return product, v1, v2, v3

    def test_reordena_segun_la_lista_recibida(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()

        result = reorder_variants(self.db, product.id, [v3.id, v1.id, v2.id])

        self.assertEqual([v.display_order for v in result], [1, 2, 3])
        self.assertEqual(result[0].id, v3.id)
        self.assertEqual(result[1].id, v1.id)
        self.assertEqual(result[2].id, v2.id)

        # Verificado también vía el mismo query que usa list_variants (FR-004 aplicado
        # al propio formulario, no solo al Menú QR).
        listado = list_variants(product.id, None, self.db, None)
        self.assertEqual([v.id for v in listado], [v3.id, v1.id, v2.id])

    def test_id_ajeno_al_producto_es_rechazado_sin_modificar_nada(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()
        ajeno = uuid4()

        with self.assertRaises(VariantReorderError) as ctx:
            reorder_variants(self.db, product.id, [v1.id, v2.id, ajeno])
        self.assertIn(ajeno, ctx.exception.extra)
        self.assertIn(v3.id, ctx.exception.missing)

        # La validación ocurre antes de tocar ninguna fila -- nada que revertir.
        self.assertEqual([v1.display_order, v2.display_order, v3.display_order], [1, 2, 3])

    def test_id_duplicado_es_rechazado(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()
        with self.assertRaises(VariantReorderError):
            reorder_variants(self.db, product.id, [v1.id, v1.id, v2.id])

    def test_id_faltante_es_rechazado(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()
        with self.assertRaises(VariantReorderError) as ctx:
            reorder_variants(self.db, product.id, [v1.id, v2.id])
        self.assertIn(v3.id, ctx.exception.missing)

    def test_id_de_presentacion_desactivada_es_rechazado(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()
        v3.active = False
        self.db.flush()
        with self.assertRaises(VariantReorderError) as ctx:
            reorder_variants(self.db, product.id, [v1.id, v2.id, v3.id])
        self.assertIn(v3.id, ctx.exception.extra)

    def test_endpoint_http_422_en_lista_invalida(self):
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()
        with self.assertRaises(HTTPException) as ctx:
            reorder_product_variants(
                product.id, VariantReorderRequest(variant_ids=[v1.id, v2.id]), self.db, None
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_menu_qr_ve_el_mismo_orden_via_la_relacion_orm(self):
        """US2/FR-004: `Product.variants` (la misma relación que recorre
        `menu/router.py`) refleja el reordenamiento, sin tocar ese router."""
        product, v1, v2, v3 = self._producto_con_tres_presentaciones()

        reorder_variants(self.db, product.id, [v2.id, v3.id, v1.id])

        self.db.expire(product)
        self.assertEqual([v.id for v in product.variants], [v2.id, v3.id, v1.id])

    def test_producto_nunca_reordenado_conserva_orden_de_creacion(self):
        """FR-009/SC-004: sin ningún reordenamiento explícito, el orden que ve el
        Menú QR (la relación ORM) es el de creación -- el mismo que produce el
        backfill de la migración (ROW_NUMBER() OVER (... ORDER BY id))."""
        product = f.make_product(self.db)
        v1 = f.make_variant(self.db, product, name="Primera")
        v2 = f.make_variant(self.db, product, name="Segunda")
        v3 = f.make_variant(self.db, product, name="Tercera")

        self.db.expire(product)
        self.assertEqual([v.id for v in product.variants], [v1.id, v2.id, v3.id])

    def test_reordenar_un_producto_no_afecta_a_otro(self):
        """FR-010: el orden es específico de cada producto."""
        product_a, a1, a2, a3 = self._producto_con_tres_presentaciones()
        product_b, b1, b2, b3 = self._producto_con_tres_presentaciones()

        reorder_variants(self.db, product_a.id, [a3.id, a2.id, a1.id])

        self.db.refresh(b1)
        self.db.refresh(b2)
        self.db.refresh(b3)
        self.assertEqual([b1.display_order, b2.display_order, b3.display_order], [1, 2, 3])


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
