"""CONGELA comportamiento corregido: app/api/v1/products/service.py:67-91
(update_product) — cierra la anomalía A-44 (specs/000-reconocimiento/
registro-de-anomalias.md, registro-riesgos.md R23).

Antes de esta corrección (specs/021-correccion-orden-borrado-imagen-r2),
`update_product` borraba el objeto de imagen anterior en Cloudflare R2 ANTES
de `db.commit()`. Si el commit fallaba después por cualquier razón ajena a la
imagen, el `product.image_url` revertía a la URL vieja en memoria/BD, pero el
objeto que esa URL señala ya había sido borrado un paso antes — el producto
quedaba apuntando a una imagen inexistente, sin ningún registro de que
ocurrió (`delete_object` es best-effort: solo loguea, nunca lanza).

Esta spec invierte el orden: `delete_object` se invoca DESPUÉS de un
`db.commit()` exitoso.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_products_service -v
"""
import unittest
from unittest import mock

from app.characterization_tests import fixtures as fx
from app.api.v1.products.service import ProductService
from app.api.v1.products.schemas import ProductUpdate

OLD_URL = "https://example.invalid/tenant/products/old.jpg"
NEW_URL = "https://example.invalid/tenant/products/new.jpg"


class TestUpdateProductA44(unittest.TestCase):
    def _seed_product(self, db):
        return fx.make_product(db, image_url=OLD_URL)

    def test_a44_fallo_de_commit_no_deja_referencia_rota(self):
        """FR-002/CA2: si el commit falla, delete_object NUNCA se llama — el
        objeto viejo en R2 y product.image_url quedan consistentes entre sí."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()

        with mock.patch(
            "app.api.v1.products.service.delete_object"
        ) as mock_delete, mock.patch.object(
            db, "commit", side_effect=RuntimeError("fallo ajeno a la imagen")
        ):
            with self.assertRaises(RuntimeError):
                service.update_product(db, product.id, ProductUpdate(image_url=NEW_URL))

        mock_delete.assert_not_called()

    def test_a44_camino_feliz_borra_despues_del_commit(self):
        """FR-001/SC-001: en éxito, delete_object se invoca después de commit
        — orden nuevo que cierra A-44, sin cambiar el resultado final."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()
        orden = []

        real_commit = db.commit

        with mock.patch(
            "app.api.v1.products.service.delete_object",
            side_effect=lambda key: orden.append("delete"),
        ) as mock_delete, mock.patch.object(
            db, "commit", side_effect=lambda: (orden.append("commit"), real_commit())
        ):
            service.update_product(db, product.id, ProductUpdate(image_url=NEW_URL))

        self.assertEqual(orden, ["commit", "delete"])
        mock_delete.assert_called_once()

    def test_a44_fallo_de_delete_object_no_revierte_el_cambio_de_imagen(self):
        """FR-004/RN2: delete_object sigue siendo best-effort — un fallo suyo
        (p. ej. R2 no disponible) no revierte el cambio de imagen ya
        persistido. delete_object ya captura toda excepción internamente
        (app/core/storage.py); este test documenta ese contrato, no agrega
        manejo de errores nuevo en update_product."""
        db = fx.new_session()
        product = self._seed_product(db)
        db.commit()
        service = ProductService()

        def _delete_falla(key):
            try:
                raise ConnectionError("R2 no disponible")
            except ConnectionError:
                pass  # delete_object real: logueado, nunca propagado

        with mock.patch(
            "app.api.v1.products.service.delete_object", side_effect=_delete_falla
        ):
            result = service.update_product(db, product.id, ProductUpdate(image_url=NEW_URL))

        self.assertEqual(result.image_url, NEW_URL)


if __name__ == "__main__":
    unittest.main()
