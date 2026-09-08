"""Tests de la nueva funcionalidad — spec 080-imagenes-key-relativa-r2, US1
(persistencia + lectura) y US3 (borrado del objeto anterior, ampliado en T022).

Al guardar un producto, `products.image_url` persiste **la key** tanto si el
cliente manda la key como si reenvía una URL absoluta del bucket gestionado
(dominio viejo o nuevo, FR-002/FR-004); una URL de otro origen se conserva
intacta (FR-010). Las respuestas (`ProductResponse` y subclases,
`MenuProductResponse`) devuelven `image_url` como URL absoluta contra
`ASSETS_BASE_URL` mientras la columna sigue con la key (FR-006/FR-007).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_image_key -v
"""
import unittest
from unittest import mock
from uuid import uuid4

from app.characterization_tests import fixtures as fx
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import (
    ProductCreate, ProductUpdate, ProductResponse, ProductListResponse,
    ProductDetailResponse,
)
from app.api.v1.menu.schemas import MenuProductResponse

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"
KEY = "heladeria3/products/46f1a4d1c4aa4c68ba7b32642334d084.png"
URL_LEGACY = f"{LEGACY}/{KEY}"
URL_NEW = f"{ASSETS}/{KEY}"
URL_OTHER = "https://xyz.supabase.co/storage/v1/object/public/img/foto.jpg"


class TestImageUrlPersistsAsKey(unittest.TestCase):
    """FR-002/FR-004/SC-001 — la columna nunca guarda esquema ni dominio."""

    def _create(self, db, image_url):
        category = fx.make_category(db)
        db.commit()
        return ProductService().create_product(
            db, fx.make_tenant_stub(),
            ProductCreate(category_id=category.id, name=f"p-{uuid4()}", image_url=image_url),
        )

    def test_key_directa_se_guarda_igual(self):
        db = fx.new_session()
        product = self._create(db, KEY)
        self.assertEqual(product.image_url, KEY)

    def test_url_publica_anterior_se_normaliza_a_key(self):
        db = fx.new_session()
        product = self._create(db, URL_LEGACY)
        self.assertEqual(product.image_url, KEY)

    def test_url_dominio_nuevo_se_normaliza_a_key(self):
        db = fx.new_session()
        product = self._create(db, URL_NEW)
        self.assertEqual(product.image_url, KEY)

    def test_otro_origen_se_conserva_intacto(self):
        db = fx.new_session()
        product = self._create(db, URL_OTHER)
        self.assertEqual(product.image_url, URL_OTHER)

    def test_sin_imagen_queda_null(self):
        db = fx.new_session()
        product = self._create(db, None)
        self.assertIsNone(product.image_url)

    def test_update_normaliza_la_forma_vieja_y_la_nueva(self):
        db = fx.new_session()
        product = self._create(db, KEY)
        svc = ProductService()
        with mock.patch("app.api.v1.products.service.delete_object"):
            svc.update_product(db, fx.make_tenant_stub(), product.id,
                               ProductUpdate(image_url=URL_LEGACY.replace(KEY, "heladeria3/products/otra.png")))
        self.assertEqual(product.image_url, "heladeria3/products/otra.png")


class TestImageUrlResponseAssembling(unittest.TestCase):
    """FR-005/FR-006/FR-007 — la respuesta trae la URL absoluta; la fila ORM sigue con la key."""

    def _product_with(self, db, image_url):
        product = fx.make_product(db, image_url=image_url)
        db.commit()
        return product

    def test_product_response_y_subclases_devuelven_url_absoluta(self):
        db = fx.new_session()
        product = self._product_with(db, KEY)
        for cls in (ProductResponse, ProductListResponse, ProductDetailResponse):
            dumped = cls.model_validate(product).model_dump()
            self.assertEqual(dumped["image_url"], URL_NEW, cls.__name__)
        # la columna leída del ORM sigue con la key (FR-006)
        self.assertEqual(product.image_url, KEY)

    def test_menu_product_response_devuelve_url_absoluta(self):
        db = fx.new_session()
        product = self._product_with(db, KEY)
        dumped = MenuProductResponse(
            id=product.id, name=product.name, image_url=product.image_url,
        ).model_dump()
        self.assertEqual(dumped["image_url"], URL_NEW)

    def test_fila_no_migrada_se_ve_igual_tolerancia_de_lectura(self):
        # FR-009b: una fila que todavía tiene la URL vieja se sirve reescrita al
        # dominio nuevo, sin romperse.
        db = fx.new_session()
        product = self._product_with(db, URL_LEGACY)
        dumped = ProductResponse.model_validate(product).model_dump()
        self.assertEqual(dumped["image_url"], URL_NEW)

    def test_otro_origen_se_muestra_sin_modificar(self):
        db = fx.new_session()
        product = self._product_with(db, URL_OTHER)
        dumped = ProductResponse.model_validate(product).model_dump()
        self.assertEqual(dumped["image_url"], URL_OTHER)

    def test_sin_imagen_no_arma_url(self):
        db = fx.new_session()
        product = self._product_with(db, None)
        dumped = ProductResponse.model_validate(product).model_dump()
        self.assertIsNone(dumped["image_url"])


class TestImageUrlPreviousObjectDeletion(unittest.TestCase):
    """US3 / FR-011/FR-012/FR-013 — el borrado best-effort del objeto anterior
    (A-44 / spec 021, intacto) acepta ahora una key directa e ignora otros
    orígenes; un guardado que no tocó la imagen no dispara borrado."""

    def _seed(self, db, image_url):
        product = fx.make_product(db, image_url=image_url)
        db.commit()
        return product

    def _update(self, db, product, image_url):
        svc = ProductService()
        with mock.patch("app.api.v1.products.service.delete_object") as md:
            svc.update_product(db, fx.make_tenant_stub(), product.id,
                               ProductUpdate(image_url=image_url))
        return md

    def test_reemplazo_con_previa_url_vieja_borra_la_key_correcta(self):
        db = fx.new_session()
        product = self._seed(db, URL_LEGACY)
        md = self._update(db, product, "heladeria3/products/nueva.png")
        md.assert_called_once_with(KEY)                       # FR-011

    def test_reemplazo_con_previa_key_borra_esa_key(self):
        db = fx.new_session()
        product = self._seed(db, KEY)
        md = self._update(db, product, "heladeria3/products/nueva.png")
        md.assert_called_once_with(KEY)                       # antes fallaba (key -> None)

    def test_reemplazo_con_previa_otro_origen_no_borra_nada(self):
        db = fx.new_session()
        product = self._seed(db, URL_OTHER)
        md = self._update(db, product, "heladeria3/products/nueva.png")
        md.assert_not_called()                                # FR-013

    def test_guardar_reenviando_la_url_de_visualizacion_no_borra(self):
        db = fx.new_session()
        product = self._seed(db, KEY)
        # el cliente reenvía la URL que recibió (dominio nuevo) sin tocar la imagen
        md = self._update(db, product, URL_NEW)
        md.assert_not_called()                                # FR-012, data-model.md §4
        self.assertEqual(product.image_url, KEY)

    def test_fallo_antes_del_commit_no_borra_y_conserva_la_referencia(self):
        db = fx.new_session()
        product = self._seed(db, KEY)
        svc = ProductService()
        with mock.patch("app.api.v1.products.service.delete_object") as md, \
             mock.patch.object(db, "commit", side_effect=RuntimeError("ajeno")):
            with self.assertRaises(RuntimeError):
                svc.update_product(db, fx.make_tenant_stub(), product.id,
                                   ProductUpdate(image_url="heladeria3/products/nueva.png"))
        md.assert_not_called()                                # FR-012 / A-44


if __name__ == "__main__":
    unittest.main()
