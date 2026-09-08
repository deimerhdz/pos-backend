"""Tests de la nueva funcionalidad — spec 080-imagenes-key-relativa-r2, US1:
`POST /uploads/presign` sigue devolviendo la `key`; el `public_url` pasa a
armarse contra el dominio de assets nuevo (`ASSETS_BASE_URL`)
(contracts/uploads-presign.md). La whitelist de carpetas y `build_object_key`
no cambian (FR-016).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_uploads_presign_key -v
"""
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

from app.characterization_tests import fixtures  # noqa: F401  - fija el entorno de Settings
from app.api.v1.uploads.router import presign_upload
from app.api.v1.uploads.schemas import PresignRequest

ASSETS = "https://assets.example.invalid"
_TENANT = SimpleNamespace(schema="heladeria3")


def _presign(folder: str, content_type: str = "image/jpeg", filename: str = "helado.jpg"):
    return presign_upload(
        PresignRequest(filename=filename, content_type=content_type, folder=folder),
        tenant=_TENANT,
        _=None,
    )


class TestUploadsPresignKey(unittest.TestCase):
    def test_key_es_tenant_folder_uuid_nunca_el_filename_del_cliente(self):
        for folder in ("products", "logo", "payment-methods"):
            resp = _presign(folder, filename="../../etc/passwd.jpg")
            self.assertTrue(resp.key.startswith(f"heladeria3/{folder}/"))
            self.assertTrue(resp.key.endswith(".jpg"))
            self.assertNotIn("passwd", resp.key)          # FR-016
            self.assertNotIn("..", resp.key)

    def test_public_url_contra_el_dominio_de_assets_nuevo(self):
        resp = _presign("products")
        self.assertEqual(resp.public_url, f"{ASSETS}/{resp.key}")
        self.assertNotIn("example.invalid/heladeria3", resp.public_url.replace(ASSETS, ""))

    def test_content_type_no_soportado_da_422(self):
        with self.assertRaises(HTTPException) as ctx:
            _presign("products", content_type="application/pdf")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_carpeta_comprobantes_sigue_rechazada(self):
        # La carpeta de comprobantes del comensal NO está en la whitelist de
        # este endpoint (FR-014/FR-016) — la validación del esquema la rechaza.
        with self.assertRaises(ValidationError):
            PresignRequest(filename="x.jpg", content_type="image/jpeg", folder="comprobantes")


if __name__ == "__main__":
    unittest.main()
