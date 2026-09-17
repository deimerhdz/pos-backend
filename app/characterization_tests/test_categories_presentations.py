"""Tests de la nueva funcionalidad — spec 083-presentaciones-y-promociones,
US2: asociación de presentaciones a una categoría vía `presentation_ids`
(FR-004, FR-008, contracts/categoria-herencia-producto.md), ejercitado
llamando directamente a los handlers del router (mismo patrón que
`test_category_display_order.py`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_categories_presentations -v
"""
import unittest
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import fixtures as fx
from app.api.v1.categories.router import create_category, update_category
from app.api.v1.categories.schemas import CategoryCreate, CategoryUpdate
from app.models.category_presentation import CategoryPresentation
from sqlalchemy import select


class CreateCategoryWithPresentationsTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

    def _links(self, category_id):
        return set(self.db.execute(
            select(CategoryPresentation.presentation_id).where(
                CategoryPresentation.category_id == category_id
            )
        ).scalars())

    def test_sin_presentation_ids_nace_sin_ninguna_asociada(self):
        cat = create_category(CategoryCreate(name="Bebidas"), self.db, None)
        self.assertEqual(self._links(cat.id), set())

    def test_con_presentation_ids_crea_las_asociaciones(self):
        p1 = fx.make_presentation(self.db, name="Pequeña")
        p2 = fx.make_presentation(self.db, name="Mediana")
        self.db.commit()

        cat = create_category(
            CategoryCreate(name="Ensaladas", presentation_ids=[p1.id, p2.id]),
            self.db, None,
        )
        self.assertEqual(self._links(cat.id), {p1.id, p2.id})

    def test_id_inexistente_lanza_404(self):
        with self.assertRaises(HTTPException) as ctx:
            create_category(
                CategoryCreate(name="Ensaladas", presentation_ids=[uuid4()]),
                self.db, None,
            )
        self.assertEqual(ctx.exception.status_code, 404)


class UpdateCategoryPresentationsTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.p1 = fx.make_presentation(self.db, name="Pequeña")
        self.p2 = fx.make_presentation(self.db, name="Mediana")
        self.p3 = fx.make_presentation(self.db, name="Grande")
        self.cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, self.cat, self.p1)
        self.db.commit()

    def _links(self):
        return set(self.db.execute(
            select(CategoryPresentation.presentation_id).where(
                CategoryPresentation.category_id == self.cat.id
            )
        ).scalars())

    def test_none_no_toca_la_asociacion_existente(self):
        update_category(self.cat.id, CategoryUpdate(description="nueva"), self.db, None)
        self.assertEqual(self._links(), {self.p1.id})

    def test_lista_vacia_desasocia_todas(self):
        update_category(self.cat.id, CategoryUpdate(presentation_ids=[]), self.db, None)
        self.assertEqual(self._links(), set())

    def test_reemplazo_total(self):
        update_category(
            self.cat.id,
            CategoryUpdate(presentation_ids=[self.p2.id, self.p3.id]),
            self.db, None,
        )
        self.assertEqual(self._links(), {self.p2.id, self.p3.id})

    def test_id_inexistente_lanza_404_y_no_deja_cambios_parciales(self):
        with self.assertRaises(HTTPException) as ctx:
            update_category(
                self.cat.id,
                CategoryUpdate(presentation_ids=[self.p2.id, uuid4()]),
                self.db, None,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_response_incluye_presentations_asociadas(self):
        updated = update_category(
            self.cat.id,
            CategoryUpdate(presentation_ids=[self.p2.id, self.p3.id]),
            self.db, None,
        )
        nombres = {p.name for p in updated.presentations}
        self.assertEqual(nombres, {"Mediana", "Grande"})


class NoRetroactivityTests(unittest.TestCase):
    """FR-008: cambiar `presentation_ids` de una categoría no modifica ninguna
    `ProductVariant` de productos ya creados en ella -- la resolución de
    presentaciones ocurre solo en `create_product`."""

    def setUp(self):
        self.db = fx.new_session()

    def test_cambiar_presentaciones_no_toca_productos_ya_creados(self):
        from app.api.v1.products.service import ProductService
        from app.api.v1.products.schemas import ProductCreate
        from app.models.product_variant import ProductVariant
        from sqlalchemy import select as sa_select

        p1 = fx.make_presentation(self.db, name="Pequeña")
        p2 = fx.make_presentation(self.db, name="Mediana")
        cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, cat, p1)
        fx.link_category_presentation(self.db, cat, p2)
        self.db.commit()

        service = ProductService()
        product = service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(category_id=cat.id, name="Ensalada César", preparation_type="prepared"),
        )
        antes = {
            v.name for v in self.db.execute(
                sa_select(ProductVariant).where(ProductVariant.product_id == product.id)
            ).scalars()
        }
        self.assertEqual(antes, {"Pequeña", "Mediana"})

        update_category(cat.id, CategoryUpdate(presentation_ids=[]), self.db, None)

        despues = {
            v.name for v in self.db.execute(
                sa_select(ProductVariant).where(ProductVariant.product_id == product.id)
            ).scalars()
        }
        self.assertEqual(despues, {"Pequeña", "Mediana"})


if __name__ == "__main__":
    unittest.main()
