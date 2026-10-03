"""Spec 093-cajero-carta-agotado (User Story 1, escenario 9): `GET /products/{id}`
(`ProductService.to_detail_response`) incluye las presentaciones activas con su precio,
sin receta ni grupos de opciones (FR-017/FR-018).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_product_detail_variants -v
"""
import unittest
from decimal import Decimal

from app.characterization_tests import product_availability_fixtures as fx
from app.api.v1.products.service import ProductService


class ProductDetailVariantsTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.service = ProductService()

    def test_detalle_incluye_presentaciones_activas_con_precio(self):
        product = fx.make_product(self.db)
        viva = fx.make_variant(self.db, product=product, name="1 litro", price=Decimal("18000"))
        fx.make_variant(self.db, product=product, name="1/2 litro", price=Decimal("10000"), active=False)
        self.db.commit()

        detail = self.service.to_detail_response(product)

        self.assertEqual(len(detail.variants), 1)
        [v] = detail.variants
        self.assertEqual(v.id, viva.id)
        self.assertEqual(v.presentation_name, "1 litro")
        self.assertEqual(v.price, Decimal("18000"))
        self.assertTrue(v.active)

    def test_detalle_no_expone_receta_ni_grupos_de_opciones(self):
        """FR-018: ninguna pantalla de la carta del Cajero expone costos, receta ni
        datos de inventario -- `VariantResponse` (el shape que usa el detalle) no
        tiene esos campos, a diferencia de `VariantSaveOut` (solo para POST/PATCH)."""
        product = fx.make_product(self.db)
        fx.make_variant(self.db, product=product, name="1 litro")
        self.db.commit()

        detail = self.service.to_detail_response(product)

        [v] = detail.variants
        self.assertFalse(hasattr(v, "recipe"))
        self.assertFalse(hasattr(v, "option_groups"))


if __name__ == "__main__":
    unittest.main()
