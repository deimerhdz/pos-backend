"""Tests de la nueva funcionalidad — spec 083-presentaciones-y-promociones,
US2: herencia automática de variantes al crear un producto sin `variants`
explícitas (FR-005, FR-006, research.md D6).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_presentations_inheritance -v
"""
import unittest
from decimal import Decimal

from sqlalchemy import select

from app.characterization_tests import fixtures as fx
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import ProductCreate
from app.models.product_variant import ProductVariant


class InheritPresentationsOnCreateTests(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()
        self.service = ProductService()

    def _variants(self, product_id):
        return self.db.execute(
            select(ProductVariant)
            .where(ProductVariant.product_id == product_id)
            .order_by(ProductVariant.display_order)
        ).scalars().all()

    def test_categoria_con_n_presentaciones_activas_hereda_n_variantes_a_precio_0(self):
        """FR-005."""
        p1 = fx.make_presentation(self.db, name="Pequeña")
        p2 = fx.make_presentation(self.db, name="Mediana")
        p3 = fx.make_presentation(self.db, name="Grande")
        cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, cat, p1)
        fx.link_category_presentation(self.db, cat, p2)
        fx.link_category_presentation(self.db, cat, p3)
        self.db.commit()

        product = self.service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(category_id=cat.id, name="Ensalada César", preparation_type="prepared"),
        )
        variants = self._variants(product.id)
        self.assertEqual(
            {v.name for v in variants}, {"Pequeña", "Mediana", "Grande"}
        )
        self.assertTrue(all(v.price == Decimal("0") for v in variants))
        self.assertTrue(all(v.active for v in variants))

    def test_categoria_sin_presentaciones_asociadas_nace_con_presentacion_unica(self):
        """FR-006 -- mismo mecanismo que ensure_default_variant, nuevo literal (A-74)."""
        cat = fx.make_category(self.db, name="Bebidas sin tamaño")
        self.db.commit()

        product = self.service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(category_id=cat.id, name="Agua", preparation_type="prepared"),
        )
        variants = self._variants(product.id)
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].name, "Presentación única")
        self.assertEqual(variants[0].price, Decimal("0"))

    def test_presentacion_inactiva_asociada_no_se_hereda(self):
        activa = fx.make_presentation(self.db, name="Pequeña", active=True)
        inactiva = fx.make_presentation(self.db, name="Grande", active=False)
        cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, cat, activa)
        fx.link_category_presentation(self.db, cat, inactiva)
        self.db.commit()

        product = self.service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(category_id=cat.id, name="Ensalada", preparation_type="prepared"),
        )
        variants = self._variants(product.id)
        self.assertEqual({v.name for v in variants}, {"Pequeña"})

    def test_presentacion_inactiva_unica_asociada_deja_categoria_efectivamente_sin_ninguna(self):
        """Si la única presentación asociada está inactiva, el producto cae en el
        camino de FR-006 ("Presentación única"), no en una lista vacía de FR-005."""
        inactiva = fx.make_presentation(self.db, name="Grande", active=False)
        cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, cat, inactiva)
        self.db.commit()

        product = self.service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(category_id=cat.id, name="Ensalada", preparation_type="prepared"),
        )
        variants = self._variants(product.id)
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].name, "Presentación única")

    def test_variants_explicitas_ignoran_las_presentaciones_de_categoria(self):
        """Si el cliente ya manda `variants`, la herencia automática no aplica."""
        from app.api.v1.catalog.schemas import VariantSaveIn

        p1 = fx.make_presentation(self.db, name="Pequeña")
        cat = fx.make_category(self.db, name="Ensaladas")
        fx.link_category_presentation(self.db, cat, p1)
        self.db.commit()

        product = self.service.create_product(
            self.db, fx.make_tenant_stub(),
            ProductCreate(
                category_id=cat.id, name="Ensalada", preparation_type="prepared",
                variants=[VariantSaveIn(name="Familiar", price=Decimal("15000"))],
            ),
        )
        variants = self._variants(product.id)
        self.assertEqual([v.name for v in variants], ["Familiar"])


if __name__ == "__main__":
    unittest.main()
