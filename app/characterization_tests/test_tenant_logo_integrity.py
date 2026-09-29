"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2 (A-92), logo del negocio
(`PATCH /tenant`, `update_tenant`): mismas reglas que la imagen de producto, con la carpeta `logo`.

- US1 (FR-001/FR-002): un formulario desactualizado no cambia el logo, pero `receipt_message` e
  `invoice_prefix` se guardan igualmente.
- US2 (FR-003): un logo nuevo debe existir en R2 (422); si R2 no responde, 503.
- US3 (FR-004/FR-005): solo keys del propio negocio y carpeta `logo`.
- US4 (FR-006): el logo anterior solo se borra si nadie más lo usa; el chequeo usa una sesión del
  negocio (`with_db(tenant.schema)`), no la sesión `shared`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_tenant_logo_integrity -v
"""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi import HTTPException

from app.characterization_tests import auth_fixtures as fx
from app.characterization_tests import fixtures as business_fx
from app.api.v1.tenant.router import update_tenant
from app.api.v1.tenant.schemas import TenantUpdate
from app.core.storage import StorageUnavailable
from app.models.payment import PaymentMethod

SCHEMA = "acme"
CUR = "acme/logo/vigente.png"
NEW = "acme/logo/nuevo.png"
STALE = "acme/logo/viejo.png"
OTHER_ORIGIN = "https://cdn.otro.com/logo.png"

EXISTS = "app.core.asset_refs.object_exists"
DELETE = "app.api.v1.tenant.router.delete_object"
WITH_DB = "app.api.v1.tenant.router.with_db"


def _seed(db, logo=CUR, **kw):
    row = fx.make_tenant(db, schema=SCHEMA, logo_url=logo, **kw)
    db.commit()
    return row


def _business_session(populate=None):
    """Sesión del negocio (productos, métodos de pago, comprobantes) que devuelve el `with_db` simulado."""
    tdb = business_fx.new_session()
    if populate:
        populate(tdb)
    return tdb


def _patch(db, row, *, exists=True, tdb=None, **body):
    """Ejecuta `update_tenant` con R2 y la sesión del negocio simulados. Devuelve (delete, exists_mock, with_db_mock)."""
    business = tdb if tdb is not None else _business_session()

    @contextmanager
    def fake_with_db(schema):
        fake_with_db.schemas.append(schema)
        yield business

    fake_with_db.schemas = []
    with mock.patch(DELETE) as delete, mock.patch(EXISTS, return_value=exists) as exists_mock, \
         mock.patch(WITH_DB, fake_with_db):
        update_tenant(TenantUpdate(**body), tenant=SimpleNamespace(id=row.id), _=None, db=db)
    return delete, exists_mock, fake_with_db


def _reload(db, row):
    db.expire_all()
    return db.get(type(row), row.id)


class TestUS1FormularioDesactualizado(unittest.TestCase):
    def test_desactualizado_conserva_el_logo_y_guarda_lo_demas(self):
        db = fx.new_session()
        row = _seed(db)
        delete, exists, _ = _patch(
            db, row, logo_url=STALE, logo_url_base=STALE, receipt_message="Gracias", invoice_prefix="ab",
        )
        row = _reload(db, row)
        self.assertEqual(row.logo_url, CUR)
        self.assertEqual(row.receipt_message, "Gracias")
        self.assertEqual(row.invoice_prefix, "AB")
        delete.assert_not_called()
        exists.assert_not_called()

    def test_al_dia_con_logo_nuevo_lo_aplica_y_borra_el_anterior(self):
        db = fx.new_session()
        row = _seed(db)
        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base=CUR)
        self.assertEqual(_reload(db, row).logo_url, NEW)
        delete.assert_called_once_with(CUR)

    def test_sin_base_y_key_valida_distinta_se_ignora(self):
        db = fx.new_session()
        row = _seed(db)
        delete, exists, _ = _patch(db, row, logo_url=NEW, receipt_message="Hola")
        row = _reload(db, row)
        self.assertEqual((row.logo_url, row.receipt_message), (CUR, "Hola"))
        delete.assert_not_called()
        exists.assert_not_called()

    def test_sin_tocar_el_logo_no_cambia_nada(self):
        db = fx.new_session()
        row = _seed(db)
        delete, exists, _ = _patch(db, row, receipt_message="Hola")
        self.assertEqual(_reload(db, row).logo_url, CUR)
        delete.assert_not_called()
        exists.assert_not_called()

    def test_base_null_explicito_sobre_negocio_sin_logo_aplica_el_primero(self):
        db = fx.new_session()
        row = _seed(db, logo=None)
        _patch(db, row, logo_url=NEW, logo_url_base=None)
        self.assertEqual(_reload(db, row).logo_url, NEW)

    def test_key_ajena_con_formulario_desactualizado_es_422_y_nada_cambia(self):
        db = fx.new_session()
        row = _seed(db)
        with self.assertRaises(HTTPException) as ctx:
            _patch(db, row, logo_url="globex/logo/x.png", logo_url_base=STALE, receipt_message="Nuevo")
        self.assertEqual(ctx.exception.status_code, 422)
        row = _reload(db, row)
        self.assertEqual((row.logo_url, row.receipt_message), (CUR, None))


class TestUS2Existencia(unittest.TestCase):
    def test_logo_inexistente_es_422_y_no_cambia_nada(self):
        db = fx.new_session()
        row = _seed(db)
        with self.assertRaises(HTTPException) as ctx:
            _patch(db, row, logo_url=NEW, logo_url_base=CUR, receipt_message="Nuevo", exists=False)
        self.assertEqual(ctx.exception.status_code, 422)
        row = _reload(db, row)
        self.assertEqual((row.logo_url, row.receipt_message), (CUR, None))

    def test_almacenamiento_caido_es_503(self):
        db = fx.new_session()
        row = _seed(db)
        with mock.patch(EXISTS, side_effect=StorageUnavailable("x")), mock.patch(DELETE) as delete:
            with self.assertRaises(HTTPException) as ctx:
                update_tenant(
                    TenantUpdate(logo_url=NEW, logo_url_base=CUR),
                    tenant=SimpleNamespace(id=row.id), _=None, db=db,
                )
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(_reload(db, row).logo_url, CUR)
        delete.assert_not_called()

    def test_vacio_y_otro_origen_no_consultan_r2(self):
        db = fx.new_session()
        row = _seed(db)
        _, exists, _ = _patch(db, row, logo_url="", logo_url_base=CUR, receipt_message="x")
        exists.assert_not_called()
        _, exists, _ = _patch(db, row, logo_url=OTHER_ORIGIN, logo_url_base=CUR)
        exists.assert_not_called()
        self.assertEqual(_reload(db, row).logo_url, OTHER_ORIGIN)

    def test_desactualizado_con_archivo_inexistente_se_ignora(self):
        db = fx.new_session()
        row = _seed(db)
        _, exists, _ = _patch(db, row, logo_url=NEW, logo_url_base=STALE, exists=False)
        exists.assert_not_called()
        self.assertEqual(_reload(db, row).logo_url, CUR)


class TestUS3AislamientoEntreNegocios(unittest.TestCase):
    BAD = [
        "globex/logo/x.png", "acme/products/x.png", "acme/logo/../products/x.png",
        "acme//logo/x.png", "acme\\logo\\x.png", "ACME/logo/x.png", "acme/logo/x%2e%2e.png",
    ]

    def test_keys_ajenas_o_mal_formadas_son_422_sin_borrar_nada(self):
        for bad in self.BAD:
            for label, kwargs in (("base=vigente", dict(logo_url_base=CUR)), ("sin base", {}),
                                  ("base obsoleta", dict(logo_url_base=STALE))):
                with self.subTest(key=bad, caso=label):
                    db = fx.new_session()
                    row = _seed(db)
                    with self.assertRaises(HTTPException) as ctx:
                        delete = _patch(db, row, logo_url=bad, **kwargs)[0]
                    self.assertEqual(ctx.exception.status_code, 422)
                    self.assertEqual(_reload(db, row).logo_url, CUR)

    def test_logo_historico_fuera_de_convencion_se_reemplaza_pero_no_se_borra(self):
        db = fx.new_session()
        row = _seed(db, logo="legacy/logo.png")
        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base="legacy/logo.png")
        self.assertEqual(_reload(db, row).logo_url, NEW)
        delete.assert_not_called()

    def test_logo_de_otro_origen_se_reemplaza_y_nunca_se_borra(self):
        db = fx.new_session()
        row = _seed(db, logo=OTHER_ORIGIN)
        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base=OTHER_ORIGIN)
        self.assertEqual(_reload(db, row).logo_url, NEW)
        delete.assert_not_called()

    def test_key_historica_igual_a_la_vigente_reenviada_no_da_422(self):
        db = fx.new_session()
        row = _seed(db, logo="legacy/logo.png")
        _patch(db, row, logo_url="legacy/logo.png", logo_url_base="legacy/logo.png", receipt_message="ok")
        row = _reload(db, row)
        self.assertEqual((row.logo_url, row.receipt_message), ("legacy/logo.png", "ok"))


class TestUS4BorradoSoloSiNadieLoUsa(unittest.TestCase):
    def test_logo_cuya_key_usa_un_producto_no_se_borra(self):
        db = fx.new_session()
        row = _seed(db)
        tdb = _business_session(lambda s: (business_fx.make_product(s, image_url=CUR), s.commit()))
        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base=CUR, tdb=tdb)
        self.assertEqual(_reload(db, row).logo_url, NEW)
        delete.assert_not_called()

    def test_logo_cuya_key_usa_un_qr_no_se_borra(self):
        db = fx.new_session()
        row = _seed(db)

        def populate(s):
            s.add(PaymentMethod(id=uuid4(), name="Nequi", type="transfer", is_cash=False, active=True,
                                payment_info={"qr": CUR}))
            s.commit()

        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base=CUR, tdb=_business_session(populate))
        delete.assert_not_called()

    def test_sin_otros_usos_se_borra(self):
        db = fx.new_session()
        row = _seed(db)
        delete, _, _ = _patch(db, row, logo_url=NEW, logo_url_base=CUR)
        delete.assert_called_once_with(CUR)

    def test_las_referencias_se_consultan_con_una_sesion_del_negocio_no_la_shared(self):
        db = fx.new_session()
        row = _seed(db)
        tdb = _business_session()
        seen = []
        with mock.patch("app.core.asset_refs.is_key_referenced",
                        side_effect=lambda session, key: (seen.append((session, key)), False)[1]):
            delete, _, fake = _patch(db, row, logo_url=NEW, logo_url_base=CUR, tdb=tdb)
        self.assertEqual(fake.schemas, [SCHEMA])
        self.assertEqual(seen, [(tdb, CUR)])
        self.assertIsNot(seen[0][0], db)
        delete.assert_called_once_with(CUR)

    def test_fallo_de_delete_object_no_revierte_el_cambio_ni_lanza(self):
        db = fx.new_session()
        row = _seed(db)
        with mock.patch(DELETE, side_effect=RuntimeError("R2 caído")), mock.patch(EXISTS, return_value=True), \
             mock.patch(WITH_DB, contextmanager(lambda schema: (yield _business_session()))):
            update_tenant(TenantUpdate(logo_url=NEW, logo_url_base=CUR), tenant=SimpleNamespace(id=row.id),
                          _=None, db=db)
        self.assertEqual(_reload(db, row).logo_url, NEW)

    def test_el_borrado_ocurre_despues_del_commit(self):
        db = fx.new_session()
        row = _seed(db)
        events = []
        real_commit = db.commit
        with mock.patch(DELETE, side_effect=lambda key: events.append("delete")), \
             mock.patch(EXISTS, return_value=True), \
             mock.patch(WITH_DB, contextmanager(lambda schema: (yield _business_session()))), \
             mock.patch.object(db, "commit", side_effect=lambda: (events.append("commit"), real_commit())):
            update_tenant(TenantUpdate(logo_url=NEW, logo_url_base=CUR), tenant=SimpleNamespace(id=row.id),
                          _=None, db=db)
        self.assertEqual(events, ["commit", "delete"])


if __name__ == "__main__":
    unittest.main()
