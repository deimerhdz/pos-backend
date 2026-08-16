"""CONGELA app/api/v1/catalog/service.py — generación de SKU por defecto y
detección de variantes duplicadas.

Referencias: `reglas-de-negocio.md` RN-CAT-06, RN-CAT-07, RN-CAT-08, RN-CAT-09.
"""
import unittest

from app.characterization_tests import fixtures as f
from app.api.v1.catalog.service import _slug, _unique_sku, ensure_default_variant, variante_duplicada


class SlugTests(unittest.TestCase):
    def test_rn_cat_06_toma_primeros_4_alfanumericos_en_mayuscula(self):
        self.assertEqual(_slug("Cono Waffle"), "CONO")

    def test_rn_cat_06_ignora_caracteres_no_alfanumericos(self):
        self.assertEqual(_slug("Café - Ñ'oño!!"), "CAFO")  # 'Ñ' no es A-Z/0-9 ASCII: se descarta

    def test_rn_cat_06_texto_vacio_o_sin_alfanumericos_produce_x(self):
        self.assertEqual(_slug(""), "X")
        self.assertEqual(_slug("---"), "X")
        self.assertEqual(_slug(None), "X")

    def test_rn_cat_06_string_corto_no_se_rellena(self):
        self.assertEqual(_slug("Té"), "T")  # "é" se descarta, queda 1 char


class UniqueSkuTests(unittest.TestCase):
    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_07_sin_colision_devuelve_el_mismo_base(self):
        self.assertEqual(_unique_sku(self.db, "CONO-DEF"), "CONO-DEF")

    def test_rn_cat_07_colision_agrega_sufijo_2_luego_3(self):
        variant1 = f.make_variant(self.db, sku="CONO-DEF")
        self.assertEqual(_unique_sku(self.db, "CONO-DEF"), "CONO-DEF-2")
        variant2 = f.make_variant(self.db, sku="CONO-DEF-2")
        self.assertEqual(_unique_sku(self.db, "CONO-DEF"), "CONO-DEF-3")


class EnsureDefaultVariantTests(unittest.TestCase):
    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_05_producto_sin_variantes_recibe_single_precio_0(self):
        product = f.make_product(self.db, name="Cono Waffle")
        variant = ensure_default_variant(self.db, product)
        self.assertEqual(variant.name, "Single")
        self.assertEqual(variant.price, 0)
        self.assertEqual(variant.sku, "CONO-DEF")
        self.assertTrue(variant.active)

    def test_rn_cat_05_producto_con_variante_existente_no_crea_otra(self):
        product = f.make_product(self.db)
        existing = f.make_variant(self.db, product=product, name="Grande")
        result = ensure_default_variant(self.db, product)
        self.assertEqual(result.id, existing.id)


class VarianteDuplicadaTests(unittest.TestCase):
    def setUp(self):
        self.db = f.new_session()

    def test_rn_cat_08_case_insensitive_y_espacios_recortados(self):
        product = f.make_product(self.db)
        f.make_variant(self.db, product=product, name="Pequeña")
        found = variante_duplicada(self.db, product.id, "  pequeña  ")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "Pequeña")

    def test_rn_cat_09_detecta_incluso_variante_desactivada(self):
        product = f.make_product(self.db)
        f.make_variant(self.db, product=product, name="Grande", active=False)
        found = variante_duplicada(self.db, product.id, "Grande")
        self.assertIsNotNone(found)
        self.assertFalse(found.active)

    def test_variante_duplicada_no_encuentra_nombre_distinto(self):
        product = f.make_product(self.db)
        f.make_variant(self.db, product=product, name="Grande")
        self.assertIsNone(variante_duplicada(self.db, product.id, "Mediana"))

    def test_variante_duplicada_respeta_exclude_id(self):
        product = f.make_product(self.db)
        v = f.make_variant(self.db, product=product, name="Grande")
        self.assertIsNone(variante_duplicada(self.db, product.id, "Grande", exclude_id=v.id))


if __name__ == "__main__":
    unittest.main()
