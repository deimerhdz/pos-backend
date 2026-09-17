"""Tests de la nueva funcionalidad — spec 084-fix-promociones-productos,
US1: asociar una variante de producto con una presentación del catálogo
(FR-001 a FR-007). Cierra el vacío que dejaba spec 083: el catálogo de
Presentaciones existía sin ninguna relación real con `ProductVariant`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_variant_presentation -v
"""
import unittest
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.characterization_tests import fixtures as fx
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import ProductUpdate
from app.api.v1.catalog.schemas import VariantSaveIn
from app.api.v1.presentations.router import update_presentation
from app.api.v1.presentations.schemas import PresentationUpdate
from app.models.product_variant import ProductVariant


def _variant(db, variant_id):
    db.expire_all()
    return db.get(ProductVariant, variant_id)


class AsociarPresentacionTests(unittest.TestCase):
    """FR-001/FR-002/FR-003: elegir una presentación autocompleta y sincroniza
    el nombre de la variante."""

    def setUp(self):
        self.db = fx.new_session()
        self.service = ProductService()

    def test_asociar_una_presentacion_al_crear_toma_su_nombre(self):
        product = fx.make_product(self.db)
        grande = fx.make_presentation(self.db, name="Grande")
        self.db.commit()

        self.service.update_product(
            self.db, fx.make_tenant_stub(), product.id,
            ProductUpdate(variants=[
                VariantSaveIn(name="lo que sea", price=Decimal("5000"), presentation_id=grande.id),
            ]),
        )
        variant = self.db.execute(
            select(ProductVariant).where(ProductVariant.product_id == product.id)
        ).scalar_one()
        self.assertEqual(variant.name, "Grande")
        self.assertEqual(variant.presentation_id, grande.id)

    def test_reasociar_a_otra_presentacion_actualiza_el_nombre(self):
        product = fx.make_product(self.db)
        grande = fx.make_presentation(self.db, name="Grande")
        mediana = fx.make_presentation(self.db, name="Mediana")
        v = fx.make_variant(self.db, product, name="Grande", presentation_id=grande.id)
        self.db.commit()

        self.service.update_product(
            self.db, fx.make_tenant_stub(), product.id,
            ProductUpdate(variants=[
                VariantSaveIn(id=v.id, name="Grande", price=v.price, presentation_id=mediana.id),
            ]),
        )
        actual = _variant(self.db, v.id)
        self.assertEqual(actual.name, "Mediana")
        self.assertEqual(actual.presentation_id, mediana.id)

    def test_sin_presentacion_conserva_nombre_libre(self):
        """FR-005: `presentation_id=None` dejar el nombre tal como venga en el payload."""
        product = fx.make_product(self.db)
        v = fx.make_variant(self.db, product, name="Vaso 12oz")
        self.db.commit()

        self.service.update_product(
            self.db, fx.make_tenant_stub(), product.id,
            ProductUpdate(variants=[
                VariantSaveIn(id=v.id, name="Vaso 12oz renombrado", price=v.price, presentation_id=None),
            ]),
        )
        actual = _variant(self.db, v.id)
        self.assertEqual(actual.name, "Vaso 12oz renombrado")
        self.assertIsNone(actual.presentation_id)

    def test_presentacion_inexistente_o_inactiva_rechazada_con_422(self):
        product = fx.make_product(self.db)
        inactiva = fx.make_presentation(self.db, name="Retirada", active=False)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self.service.update_product(
                self.db, fx.make_tenant_stub(), product.id,
                ProductUpdate(variants=[
                    VariantSaveIn(name="x", price=Decimal("0"), presentation_id=inactiva.id),
                ]),
            )
        self.assertEqual(ctx.exception.status_code, 422)

        with self.assertRaises(HTTPException) as ctx2:
            self.service.update_product(
                self.db, fx.make_tenant_stub(), product.id,
                ProductUpdate(variants=[
                    VariantSaveIn(name="x", price=Decimal("0"), presentation_id=uuid4()),
                ]),
            )
        self.assertEqual(ctx2.exception.status_code, 422)


class UnicidadPorProductoTests(unittest.TestCase):
    """FR-006: dos variantes del mismo producto no pueden compartir presentación."""

    def setUp(self):
        self.db = fx.new_session()
        self.service = ProductService()

    def test_dos_variantes_del_mismo_producto_no_comparten_presentacion(self):
        product = fx.make_product(self.db)
        grande = fx.make_presentation(self.db, name="Grande")
        fx.make_variant(self.db, product, name="Grande", presentation_id=grande.id)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self.service.update_product(
                self.db, fx.make_tenant_stub(), product.id,
                ProductUpdate(variants=[
                    VariantSaveIn(name="otra", price=Decimal("0"), presentation_id=grande.id),
                ]),
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_guardar_sin_cambios_la_misma_variante_no_choca_consigo_misma(self):
        """La variante que ya tiene la presentación puede guardarse de nuevo sin 409."""
        product = fx.make_product(self.db)
        grande = fx.make_presentation(self.db, name="Grande")
        v = fx.make_variant(self.db, product, name="Grande", presentation_id=grande.id, price=Decimal("1000"))
        self.db.commit()

        self.service.update_product(
            self.db, fx.make_tenant_stub(), product.id,
            ProductUpdate(variants=[
                VariantSaveIn(id=v.id, name="Grande", price=Decimal("1200"), presentation_id=grande.id),
            ]),
        )
        actual = _variant(self.db, v.id)
        self.assertEqual(actual.price, Decimal("1200.00"))

    def test_misma_presentacion_en_productos_distintos_esta_permitida(self):
        grande = fx.make_presentation(self.db, name="Grande")
        p1 = fx.make_product(self.db)
        p2 = fx.make_product(self.db)
        fx.make_variant(self.db, p1, name="Grande", presentation_id=grande.id)
        self.db.commit()

        # No debe lanzar -- son productos distintos.
        self.service.update_product(
            self.db, fx.make_tenant_stub(), p2.id,
            ProductUpdate(variants=[
                VariantSaveIn(name="otra", price=Decimal("0"), presentation_id=grande.id),
            ]),
        )
        variant = self.db.execute(
            select(ProductVariant).where(ProductVariant.product_id == p2.id)
        ).scalar_one()
        self.assertEqual(variant.presentation_id, grande.id)


class CascadaDeRenombreTests(unittest.TestCase):
    """FR-004: renombrar una presentación actualiza toda variante asociada."""

    def setUp(self):
        self.db = fx.new_session()

    def test_renombrar_actualiza_todas_las_variantes_asociadas(self):
        grande = fx.make_presentation(self.db, name="Grande")
        p1 = fx.make_product(self.db)
        p2 = fx.make_product(self.db)
        v1 = fx.make_variant(self.db, p1, name="Grande", presentation_id=grande.id)
        v2 = fx.make_variant(self.db, p2, name="Grande", presentation_id=grande.id)
        # Variante sin presentación -- no debe verse afectada.
        v3 = fx.make_variant(self.db, p1, name="Grande (manual)")
        self.db.commit()

        update_presentation(grande.id, PresentationUpdate(name="Extra Grande"), self.db, None)

        self.assertEqual(_variant(self.db, v1.id).name, "Extra Grande")
        self.assertEqual(_variant(self.db, v2.id).name, "Extra Grande")
        self.assertEqual(_variant(self.db, v3.id).name, "Grande (manual)")

    def test_renombre_que_colisiona_con_variante_sin_presentacion_se_rechaza(self):
        """Edge case agregado en /speckit-analyze (spec.md FR-004): si el nuevo nombre ya lo
        usa otra variante sin presentación del mismo producto, se rechaza todo el renombre."""
        grande = fx.make_presentation(self.db, name="Grande")
        product = fx.make_product(self.db)
        v1 = fx.make_variant(self.db, product, name="Grande", presentation_id=grande.id)
        v2 = fx.make_variant(self.db, product, name="Súper")
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            update_presentation(grande.id, PresentationUpdate(name="Súper"), self.db, None)
        self.assertEqual(ctx.exception.status_code, 409)

        # Ninguna fila cambió: ni la presentación ni las variantes.
        self.assertEqual(_variant(self.db, v1.id).name, "Grande")
        self.assertEqual(_variant(self.db, v2.id).name, "Súper")
        self.db.expire_all()
        self.assertEqual(self.db.get(type(grande), grande.id).name, "Grande")

    def test_colision_en_otro_producto_no_bloquea_el_renombre(self):
        """La colisión solo importa dentro del MISMO producto (FR-006/FR-004)."""
        grande = fx.make_presentation(self.db, name="Grande")
        p1 = fx.make_product(self.db)
        p2 = fx.make_product(self.db)
        v1 = fx.make_variant(self.db, p1, name="Grande", presentation_id=grande.id)
        # Variante de OTRO producto con el nombre que colisionaría -- no debe importar.
        fx.make_variant(self.db, p2, name="Súper")
        self.db.commit()

        update_presentation(grande.id, PresentationUpdate(name="Súper"), self.db, None)
        self.assertEqual(_variant(self.db, v1.id).name, "Súper")


if __name__ == "__main__":
    unittest.main()
