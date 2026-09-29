"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2, primitivas de
`app/core/storage.py` (FR-003, FR-004, FR-005): `validate_asset_key`, `object_exists` y
`deletable_key`, más la configuración del cliente de R2.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_storage_asset_guard -v
"""
import unittest
from unittest import mock

from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.characterization_tests import fixtures  # noqa: F401  - variables de entorno inertes
from app.core import storage
from app.core.storage import (
    AssetKeyError,
    StorageUnavailable,
    deletable_key,
    object_exists,
    validate_asset_key,
)

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"
KEY = "acme/products/46f1a4d1c4aa4c68ba7b32642334d084.png"


class TestValidateAssetKey(unittest.TestCase):
    def _ok(self, value, schema="acme", folder="products"):
        self.assertEqual(validate_asset_key(value, schema, folder), value)

    def _bad(self, value, schema="acme", folder="products"):
        with self.assertRaises(AssetKeyError, msg=repr(value)):
            validate_asset_key(value, schema, folder)

    def test_keys_validas(self):
        self._ok(KEY)
        self._ok("acme/products/foto.v2.jpg")
        self._ok("acme/logo/x.png", folder="logo")
        self._ok("acme/payment-methods/x.png", folder="payment-methods")
        self._ok("acme/comprobantes/x.jpg", folder="comprobantes")

    def test_otro_negocio(self):
        self._bad("globex/products/x.jpg")

    def test_otra_carpeta(self):
        self._bad("acme/logo/x.png")
        self._bad("acme/products/x.jpg", folder="logo")

    def test_mayusculas_distintas(self):
        self._bad("ACME/products/x.jpg")
        self._bad("acme/Products/x.jpg")

    def test_recorridos_y_separadores(self):
        self._bad("acme/products/../logo/x.png")
        self._bad("acme//products/x.jpg")
        self._bad("acme/products//x.jpg")
        self._bad("acme\\products\\x.jpg")
        self._bad("acme/products/a..b.jpg")

    def test_caracteres_de_control_y_codificados(self):
        self._bad("acme/products/x.jpg\n")
        self._bad("acme/products/x\x00.jpg")
        self._bad("acme/products/%2e%2e/x.jpg")
        self._bad("acme/products/x%2e%2e.jpg")
        self._bad("acme/products/a b.jpg")

    def test_sin_nombre_o_nombre_invalido(self):
        self._bad("acme/products/")
        self._bad("acme/products/.hidden")
        self._bad("acme/products/a/b.jpg")

    def test_vacio(self):
        self._bad("")

    def test_esquema_con_metacaracteres_se_escapa(self):
        self._ok("a.c/products/x.jpg", schema="a.c")
        self._bad("abc/products/x.jpg", schema="a.c")

    def test_nombre_demasiado_largo(self):
        self._ok(f"acme/products/{'a' * 200}")
        self._bad(f"acme/products/{'a' * 201}")


def _client_error(code, status=None):
    return ClientError(
        {"Error": {"Code": code, "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": status or 0}},
        "HeadObject",
    )


class TestObjectExists(unittest.TestCase):
    def _exists_with(self, head_side_effect):
        client = mock.Mock()
        client.head_object.side_effect = head_side_effect
        with mock.patch("app.core.storage.get_r2_client", return_value=client):
            return object_exists(KEY), client

    def test_200_existe(self):
        result, client = self._exists_with(None)
        self.assertTrue(result)
        client.head_object.assert_called_once()
        self.assertEqual(client.head_object.call_args.kwargs["Key"], KEY)

    def test_no_existe_devuelve_false(self):
        for code in ("404", "NoSuchKey", "NotFound"):
            with self.subTest(code=code):
                result, _ = self._exists_with(_client_error(code))
                self.assertFalse(result)

    def test_cualquier_otro_error_es_storage_unavailable(self):
        errors = [
            _client_error("403"),
            _client_error("AccessDenied"),
            _client_error("500"),
            _client_error("503"),
            EndpointConnectionError(endpoint_url="https://x.invalid"),
            ConnectTimeoutError(endpoint_url="https://x.invalid"),
            ReadTimeoutError(endpoint_url="https://x.invalid"),
        ]
        for exc in errors:
            with self.subTest(exc=type(exc).__name__, code=getattr(exc, "response", None)):
                client = mock.Mock()
                client.head_object.side_effect = exc
                with mock.patch("app.core.storage.get_r2_client", return_value=client):
                    with self.assertRaises(StorageUnavailable):
                        object_exists(KEY)

    def test_nunca_escribe_ni_borra(self):
        client = mock.Mock()
        with mock.patch("app.core.storage.get_r2_client", return_value=client):
            object_exists(KEY)
        client.delete_object.assert_not_called()
        client.put_object.assert_not_called()


class TestDeletableKey(unittest.TestCase):
    def test_vacios(self):
        self.assertIsNone(deletable_key(None, "acme", "products"))
        self.assertIsNone(deletable_key("", "acme", "products"))
        self.assertIsNone(deletable_key("   ", "acme", "products"))

    def test_url_de_otro_origen_no_se_borra(self):
        self.assertIsNone(deletable_key("https://cdn.otro.com/acme/products/x.jpg", "acme", "products"))

    def test_key_en_convencion(self):
        self.assertEqual(deletable_key(KEY, "acme", "products"), KEY)

    def test_url_gestionada_en_convencion(self):
        self.assertEqual(deletable_key(f"{ASSETS}/{KEY}", "acme", "products"), KEY)
        self.assertEqual(deletable_key(f"{LEGACY}/{KEY}", "acme", "products"), KEY)

    def test_key_historica_fuera_de_convencion(self):
        self.assertIsNone(deletable_key("legacy/img/foto.jpg", "acme", "products"))
        self.assertIsNone(deletable_key("foto.jpg", "acme", "products"))

    def test_key_de_otro_negocio_o_carpeta(self):
        self.assertIsNone(deletable_key("globex/products/x.jpg", "acme", "products"))
        self.assertIsNone(deletable_key("acme/logo/x.png", "acme", "products"))


class TestR2ClientConfig(unittest.TestCase):
    def test_timeouts_y_reintentos(self):
        storage.get_r2_client.cache_clear()
        try:
            config = storage.get_r2_client().meta.config
            self.assertEqual(config.connect_timeout, 3)
            self.assertEqual(config.read_timeout, 5)
            # 2 intentos en total (no 2 reintentos): peor caso ~16 s, research D3.
            self.assertEqual(config.retries["total_max_attempts"], 2)
            self.assertEqual(config.retries["mode"], "standard")
        finally:
            storage.get_r2_client.cache_clear()


if __name__ == "__main__":
    unittest.main()
