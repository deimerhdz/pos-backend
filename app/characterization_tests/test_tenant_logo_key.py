"""Tests de la nueva funcionalidad — spec 080-imagenes-key-relativa-r2, US1/US2/US3
para `Tenant.logo_url`.

`PATCH /tenant` persiste la key (forma vieja y nueva se normalizan, FR-002/FR-004);
`GET /tenant` (`TenantInfoResponse`) y `GET /menu` (`MenuBusinessResponse`)
devuelven la URL absoluta contra `ASSETS_BASE_URL` (FR-005/FR-007); una fila no
migrada se sigue viendo (FR-009b); el borrado del logo anterior se preserva
(A-44) y acepta ahora una key directa (US3, FR-011/FR-013).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_tenant_logo_key -v
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from app.characterization_tests import auth_fixtures as fx
from app.api.v1.tenant.router import update_tenant
from app.api.v1.tenant.schemas import TenantUpdate, TenantInfoResponse
from app.api.v1.menu.schemas import MenuBusinessResponse

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"
KEY = "heladeria3/logo/46f1a4d1c4aa4c68ba7b32642334d084.png"
URL_LEGACY = f"{LEGACY}/{KEY}"
URL_NEW = f"{ASSETS}/{KEY}"
URL_OTHER = "https://xyz.supabase.co/storage/v1/object/public/img/logo.jpg"


def _patch_tenant(db, row, **body):
    return update_tenant(TenantUpdate(**body), tenant=SimpleNamespace(id=row.id), _=None, db=db)


class TestLogoUrlPersistsAsKey(unittest.TestCase):
    def test_key_directa_se_guarda_igual(self):
        db = fx.new_session()
        row = fx.make_tenant(db)
        db.commit()
        with mock.patch("app.api.v1.tenant.router.delete_object"):
            _patch_tenant(db, row, logo_url=KEY)
        db.refresh(row)
        self.assertEqual(row.logo_url, KEY)

    def test_url_publica_anterior_se_normaliza_a_key(self):
        db = fx.new_session()
        row = fx.make_tenant(db)
        db.commit()
        with mock.patch("app.api.v1.tenant.router.delete_object"):
            _patch_tenant(db, row, logo_url=URL_LEGACY)
        db.refresh(row)
        self.assertEqual(row.logo_url, KEY)

    def test_url_dominio_nuevo_se_normaliza_a_key(self):
        db = fx.new_session()
        row = fx.make_tenant(db)
        db.commit()
        with mock.patch("app.api.v1.tenant.router.delete_object"):
            _patch_tenant(db, row, logo_url=URL_NEW)
        db.refresh(row)
        self.assertEqual(row.logo_url, KEY)

    def test_otro_origen_se_conserva(self):
        db = fx.new_session()
        row = fx.make_tenant(db)
        db.commit()
        with mock.patch("app.api.v1.tenant.router.delete_object"):
            _patch_tenant(db, row, logo_url=URL_OTHER)
        db.refresh(row)
        self.assertEqual(row.logo_url, URL_OTHER)


class TestLogoUrlResponseAssembling(unittest.TestCase):
    def test_tenant_info_response_devuelve_url_absoluta(self):
        db = fx.new_session()
        row = fx.make_tenant(db, logo_url=KEY)
        db.commit()
        dumped = TenantInfoResponse.model_validate(row).model_dump()
        self.assertEqual(dumped["logo_url"], URL_NEW)
        self.assertEqual(row.logo_url, KEY)  # la columna no cambia

    def test_menu_business_response_devuelve_url_absoluta(self):
        db = fx.new_session()
        row = fx.make_tenant(db, logo_url=KEY)
        db.commit()
        dumped = MenuBusinessResponse.model_validate(row).model_dump()
        self.assertEqual(dumped["logo_url"], URL_NEW)

    def test_fila_no_migrada_se_ve_igual(self):
        # FR-009b — tolerancia de lectura
        db = fx.new_session()
        row = fx.make_tenant(db, logo_url=URL_LEGACY)
        db.commit()
        dumped = TenantInfoResponse.model_validate(row).model_dump()
        self.assertEqual(dumped["logo_url"], URL_NEW)
        # si esa fila se vuelve a guardar, queda como key (FR-004)
        with mock.patch("app.api.v1.tenant.router.delete_object"):
            _patch_tenant(db, row, logo_url=URL_LEGACY)
        db.refresh(row)
        self.assertEqual(row.logo_url, KEY)

    def test_logo_vacio_no_arma_url(self):
        db = fx.new_session()
        row = fx.make_tenant(db, logo_url=None)
        db.commit()
        dumped = TenantInfoResponse.model_validate(row).model_dump()
        self.assertIsNone(dumped["logo_url"])


class TestLogoPreviousObjectDeletion(unittest.TestCase):
    """US3 — FR-011/FR-012/FR-013 para el logo."""

    def _seed(self, db, logo_url):
        row = fx.make_tenant(db, logo_url=logo_url)
        db.commit()
        return row

    def test_reemplazo_con_previa_url_vieja_borra_la_key(self):
        db = fx.new_session()
        row = self._seed(db, URL_LEGACY)
        with mock.patch("app.api.v1.tenant.router.delete_object") as md:
            _patch_tenant(db, row, logo_url="heladeria3/logo/nuevo.png")
        md.assert_called_once_with(KEY)

    def test_reemplazo_con_previa_key_borra_esa_key(self):
        db = fx.new_session()
        row = self._seed(db, KEY)
        with mock.patch("app.api.v1.tenant.router.delete_object") as md:
            _patch_tenant(db, row, logo_url="heladeria3/logo/nuevo.png")
        md.assert_called_once_with(KEY)

    def test_reemplazo_con_previa_otro_origen_no_borra(self):
        db = fx.new_session()
        row = self._seed(db, URL_OTHER)
        with mock.patch("app.api.v1.tenant.router.delete_object") as md:
            _patch_tenant(db, row, logo_url="heladeria3/logo/nuevo.png")
        md.assert_not_called()

    def test_guardar_sin_tocar_el_logo_no_borra(self):
        db = fx.new_session()
        row = self._seed(db, KEY)
        with mock.patch("app.api.v1.tenant.router.delete_object") as md:
            _patch_tenant(db, row, logo_url=URL_NEW)  # reenvía la URL de visualización
        md.assert_not_called()
        db.refresh(row)
        self.assertEqual(row.logo_url, KEY)


if __name__ == "__main__":
    unittest.main()
