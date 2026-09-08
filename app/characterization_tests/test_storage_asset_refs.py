"""Tests de la nueva funcionalidad — spec 080-imagenes-key-relativa-r2, Fase 2:
tabla de verdad de los tres helpers puros de `app/core/storage.py` que
gobiernan cómo se guarda, se muestra y se borra una imagen de producto / logo /
método de pago (contracts/asset-reference.md, data-model.md §2–§3).

`ASSETS_BASE_URL` / `R2_PUBLIC_BASE_URL` de test se fijan en
`app/characterization_tests/fixtures.py` (importado abajo): en test valen
`https://assets.example.invalid` y `https://example.invalid`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_storage_asset_refs -v
"""
import unittest
from unittest import mock

from app.characterization_tests import fixtures  # noqa: F401  - fija el entorno de Settings
from app.core.config import settings
from app.core.storage import (
    asset_display_url,
    normalize_asset_ref,
    object_key_for_deletion,
    _managed_bucket_prefixes,
)

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"

KEY = "heladeria3/products/46f1a4d1c4aa4c68ba7b32642334d084.png"
URL_LEGACY = f"{LEGACY}/{KEY}"          # V2 — URL pública anterior del bucket gestionado
URL_NEW = f"{ASSETS}/{KEY}"             # V3 — URL del dominio personalizado nuevo
URL_OTHER = "https://xyz.supabase.co/storage/v1/object/public/img/foto.jpg"  # V4 — otro origen


class TestManagedBucketPrefixes(unittest.TestCase):
    def test_reconoce_ambos_dominios_y_dedupe(self):
        prefixes = _managed_bucket_prefixes()
        self.assertIn(f"{ASSETS}/", prefixes)
        self.assertIn(f"{LEGACY}/", prefixes)
        # de-duplicado (nunca dos entradas iguales)
        self.assertEqual(len(prefixes), len(set(prefixes)))

    def test_dedupe_cuando_coinciden(self):
        with mock.patch.object(settings, "R2_PUBLIC_BASE_URL", ASSETS):
            self.assertEqual(_managed_bucket_prefixes(), (f"{ASSETS}/",))


class TestNormalizeAssetRef(unittest.TestCase):
    """Se aplica al PERSISTIR (FR-002/FR-004): en base de datos nunca queda dominio."""

    def test_vacio_a_none(self):
        for empty in (None, "", "   "):
            self.assertIsNone(normalize_asset_ref(empty))

    def test_key_intacta(self):
        self.assertEqual(normalize_asset_ref(KEY), KEY)

    def test_url_publica_anterior_a_key(self):
        self.assertEqual(normalize_asset_ref(URL_LEGACY), KEY)

    def test_url_dominio_nuevo_a_key(self):
        self.assertEqual(normalize_asset_ref(URL_NEW), KEY)

    def test_url_con_query_string_conserva_la_query_en_la_key(self):
        self.assertEqual(normalize_asset_ref(f"{URL_NEW}?v=2"), f"{KEY}?v=2")

    def test_otro_origen_intacto(self):
        self.assertEqual(normalize_asset_ref(URL_OTHER), URL_OTHER)


class TestAssetDisplayUrl(unittest.TestCase):
    """Se aplica al RESPONDER (FR-005/FR-006): la URL solo existe en la respuesta."""

    def test_vacio_a_none(self):
        for empty in (None, "", "   "):
            self.assertIsNone(asset_display_url(empty))

    def test_key_antepone_dominio_nuevo(self):
        self.assertEqual(asset_display_url(KEY), URL_NEW)

    def test_url_publica_anterior_se_reescribe_al_dominio_nuevo(self):
        # tolerancia de lectura (FR-009b): una fila no migrada se ve igual
        self.assertEqual(asset_display_url(URL_LEGACY), URL_NEW)

    def test_url_dominio_nuevo_intacta(self):
        self.assertEqual(asset_display_url(URL_NEW), URL_NEW)

    def test_otro_origen_intacto(self):
        self.assertEqual(asset_display_url(URL_OTHER), URL_OTHER)


class TestObjectKeyForDeletion(unittest.TestCase):
    """Se aplica al BORRAR el objeto anterior (FR-011/FR-013)."""

    def test_vacio_a_none(self):
        for empty in (None, "", "   "):
            self.assertIsNone(object_key_for_deletion(empty))

    def test_key_directa(self):
        self.assertEqual(object_key_for_deletion(KEY), KEY)

    def test_url_publica_anterior_a_key(self):
        self.assertEqual(object_key_for_deletion(URL_LEGACY), KEY)

    def test_url_dominio_nuevo_a_key(self):
        self.assertEqual(object_key_for_deletion(URL_NEW), KEY)

    def test_otro_origen_no_se_borra(self):
        self.assertIsNone(object_key_for_deletion(URL_OTHER))


class TestInvariantes(unittest.TestCase):
    """data-model.md §3 §Invariantes."""

    def test_normalize_de_display_es_idempotente_para_v0_v1_v4(self):
        for value in (None, KEY, URL_OTHER):
            self.assertEqual(
                normalize_asset_ref(asset_display_url(value)),
                normalize_asset_ref(value),
            )

    def test_display_de_normalize_igual_para_v1(self):
        self.assertEqual(asset_display_url(normalize_asset_ref(KEY)), asset_display_url(KEY))

    def test_object_key_none_sii_v0_o_v4(self):
        self.assertIsNone(object_key_for_deletion(None))
        self.assertIsNone(object_key_for_deletion(URL_OTHER))
        self.assertIsNotNone(object_key_for_deletion(KEY))
        self.assertIsNotNone(object_key_for_deletion(URL_LEGACY))
        self.assertIsNotNone(object_key_for_deletion(URL_NEW))


class TestConfiguracionPura(unittest.TestCase):
    """FR-008 / SC-005: el dominio de visualización es configuración pura —
    cambiar `ASSETS_BASE_URL` reapunta la URL sin tocar ningún valor de entrada."""

    def test_cambiar_assets_base_url_reapunta_sin_migracion(self):
        nuevo = "https://cdn.otra-marca.invalid"
        with mock.patch.object(settings, "ASSETS_BASE_URL", nuevo):
            self.assertEqual(asset_display_url(KEY), f"{nuevo}/{KEY}")
            # la URL vieja del bucket gestionado también se reescribe al nuevo dominio
            self.assertEqual(asset_display_url(URL_LEGACY), f"{nuevo}/{KEY}")
            # y persistir sigue guardando solo la key (sin dominio)
            self.assertEqual(normalize_asset_ref(f"{nuevo}/{KEY}"), KEY)


if __name__ == "__main__":
    unittest.main()
