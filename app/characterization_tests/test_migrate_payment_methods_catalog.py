"""Tests de la nueva funcionalidad — spec 032-catalogo-metodos-pago, migración
de datos existentes (FR-015/FR-015a).

`app/scripts/migrate_payment_methods_catalog.py` normalmente abre su propia
conexión vía `app.core.db.with_db` (Postgres real, todos los tenants) — para
probar `_process_tenant` sin esa dependencia, se parchea `with_db` para que
entregue una sesión SQLite en memoria de `payment_catalog_fixtures`
(`contextmanager`, mismo protocolo que el real).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_migrate_payment_methods_catalog -v
"""
import unittest
from contextlib import contextmanager
from unittest import mock

from app.characterization_tests import payment_catalog_fixtures as fx
from app.scripts.migrate_payment_methods_catalog import _normalize, _process_tenant


class TestNormalize(unittest.TestCase):
    def test_normaliza_tildes_y_mayusculas(self):
        self.assertEqual(_normalize("Nequí"), "nequi")
        self.assertEqual(_normalize("  NEQUI  "), "nequi")
        self.assertEqual(_normalize("Transferencia Bancolombia"), "transferencia bancolombia")


class TestProcessTenant(unittest.TestCase):
    def setUp(self):
        self.db = fx.new_session()

        @contextmanager
        def fake_with_db(schema):
            yield self.db

        self._patcher = mock.patch(
            "app.scripts.migrate_payment_methods_catalog.with_db", fake_with_db,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_reporte_no_escribe_nada(self):
        """FR-015a: `--report-only` (write=False) no toca `catalog_id`."""
        catalog = fx.make_payment_method_catalog(
            self.db, name="Nequi", type="transfer",
            fields=[{"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10}],
        )
        method = fx.make_payment_method(
            self.db, name="Nequi", is_cash=False, type="transfer",
            payment_info={"celular": "3001234567"}, catalog_id=None,
        )
        self.db.commit()

        report = _process_tenant(
            "tenant_x", {"nequi": {"id": catalog.id, "fields": catalog.fields}}, write=False,
        )

        self.assertEqual(report.matched, ["Nequi"])
        self.db.refresh(method)
        self.assertIsNone(method.catalog_id)

    def test_backfill_setea_catalog_id_y_recalcula_is_complete(self):
        """FR-015: preserva `payment_info` ya capturado, sin pedir que se
        vuelva a diligenciar; solo calcula `is_complete` contra `catalog.fields`."""
        catalog = fx.make_payment_method_catalog(
            self.db, name="Nequi", type="transfer",
            fields=[{"key": "celular", "label": "Celular", "required": True, "format": "numeric", "length": 10}],
        )
        completo = fx.make_payment_method(
            self.db, name="Nequi", is_cash=False, type="transfer",
            payment_info={"celular": "3001234567"}, catalog_id=None,
        )
        self.db.commit()

        report = _process_tenant(
            "tenant_x", {"nequi": {"id": catalog.id, "fields": catalog.fields}}, write=True,
        )

        self.assertEqual(report.matched, ["Nequi"])
        self.db.refresh(completo)
        self.assertEqual(completo.catalog_id, catalog.id)
        self.assertTrue(completo.is_complete)
        self.assertEqual(completo.payment_info, {"celular": "3001234567"})  # sin pedir recaptura

    def test_metodo_personalizado_sin_match_se_reporta_no_se_pierde(self):
        """FR-015a: un método fuera de los tres conocidos no se pierde — se
        reporta como `unmatched` para que el Super Admin decida."""
        fx.make_payment_method(self.db, name="Daviplata", is_cash=False, type="transfer", catalog_id=None)
        self.db.commit()

        report = _process_tenant("tenant_x", {}, write=True)

        self.assertEqual(report.unmatched, ["Daviplata"])
        self.assertEqual(report.matched, [])

    def test_fila_ya_migrada_no_se_reprocesa(self):
        """Reejecutable: una fila con `catalog_id` ya poblado no se toca de nuevo."""
        catalog = fx.make_payment_method_catalog(self.db, name="Efectivo", type="cash")
        fx.make_payment_method(
            self.db, name="Efectivo", is_cash=True, type="cash", catalog_id=catalog.id,
        )
        self.db.commit()

        report = _process_tenant(
            "tenant_x", {"efectivo": {"id": catalog.id, "fields": catalog.fields}}, write=True,
        )

        self.assertEqual(report.already_migrated, ["Efectivo"])
        self.assertEqual(report.matched, [])


if __name__ == "__main__":
    unittest.main()
