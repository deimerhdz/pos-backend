"""Tests de la nueva funcionalidad — spec 088-integridad-referencias-r2 (A-93), US5:
`app/scripts/migrate_receipt_keys.py` (contracts/scripts.md §2, data-model.md §2).

`with_db` se parchea para entregar sesiones SQLite en memoria (una por esquema, más la global con
`shared.tenants`) y `object_exists` se simula; mismo patrón que `test_migrate_image_keys_relative.py`.

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_migrate_receipt_keys -v
"""
import io
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from datetime import datetime
from unittest import mock

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.characterization_tests import fixtures  # noqa: F401  - fija el entorno de Settings
from app.core.models import Base, Tenant
import app.models  # noqa: F401
from app.core.storage import StorageUnavailable
from app.models.order_payment_attempt import OrderPaymentAttempt
from app.scripts import migrate_receipt_keys as mig

ASSETS = "https://assets.example.invalid"
LEGACY = "https://example.invalid"


def _key(schema, name="46f1a4d1c4aa4c68ba7b32642334d084.jpg", folder="comprobantes"):
    return f"{schema}/{folder}/{name}"


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


def _new_session(*, tenants=None) -> Session:
    tables = [t for t in Base.metadata.tables.values() if t.name == "order_payment_attempts"]
    engine = create_engine("sqlite:///:memory:")
    conn = engine.connect().execution_options(schema_translate_map={"tenant": None})
    conn.execute(text("ATTACH DATABASE ':memory:' AS shared"))
    Base.metadata.create_all(bind=conn, tables=tables + [Tenant.__table__])
    conn.commit()
    db = Session(bind=conn, autoflush=False)
    for i, schema in enumerate(tenants or [], start=1):
        db.add(Tenant(id=i, name=f"n-{schema}", schema=schema, host=f"{schema}.local", plan_id=uuid.uuid4()))
    db.commit()
    return db


def _attempt(db, receipt):
    attempt = OrderPaymentAttempt(
        id=uuid.uuid4(), order_id=uuid.uuid4(), payment_method_id=uuid.uuid4(), status="pendiente",
        receipt_file_url=receipt, created_at=datetime.now(),
    )
    db.add(attempt)
    db.commit()
    return attempt


class TestMigrateReceiptKeys(unittest.TestCase):
    def setUp(self):
        self.shared = _new_session(tenants=["acme", "globex"])
        self.sessions = {None: self.shared, "acme": _new_session(), "globex": _new_session()}

        @contextmanager
        def fake_with_db(schema):
            yield self.sessions[schema]

        for patcher in (
            mock.patch.object(mig, "with_db", fake_with_db),
            mock.patch.object(mig, "object_exists", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        acme = self.sessions["acme"]
        self.rows = {
            "legacy": _attempt(acme, f"{LEGACY}/{_key('acme', 'a.jpg')}"),
            "assets": _attempt(acme, f"{ASSETS}/{_key('acme', 'b.jpg')}"),
            "key": _attempt(acme, _key("acme", "c.jpg")),
            "null": _attempt(acme, None),
            "otro_origen": _attempt(acme, "https://cdn.otro.com/comprobante.jpg"),
            "fuera_de_convencion": _attempt(acme, f"{LEGACY}/legacy/img/comprobante.jpg"),
            "otra_carpeta": _attempt(acme, f"{LEGACY}/acme/products/x.jpg"),
            "de_otro_negocio": _attempt(acme, f"{LEGACY}/globex/comprobantes/x.jpg"),
        }
        self.globex_row = _attempt(self.sessions["globex"], f"{LEGACY}/{_key('globex', 'g.jpg')}")

    def _value(self, name):
        session = self.sessions["acme"]
        session.expire_all()
        return session.get(OrderPaymentAttempt, self.rows[name].id).receipt_file_url

    def _run(self, **kwargs):
        out = io.StringIO()
        with redirect_stdout(out):
            code = mig.run(**kwargs)
        return code, out.getvalue()

    # ------------------------------------------------------------------ simulación
    def test_simulacion_por_defecto_no_modifica_nada(self):
        before = {name: self._value(name) for name in self.rows}
        code, out = self._run(write=False, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual({name: self._value(name) for name in self.rows}, before)
        self.assertIn("SIMULACIÓN", out)
        self.assertIn("2 a reescribir", out)          # acme: legacy + assets
        self.assertIn("1 a reescribir", out)          # globex

    # ------------------------------------------------------------------ aplicación
    def test_apply_reescribe_url_gestionada_con_key_en_convencion_y_archivo_existente(self):
        code, _ = self._run(write=True, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual(self._value("legacy"), _key("acme", "a.jpg"))
        self.assertEqual(self._value("assets"), _key("acme", "b.jpg"))
        self.sessions["globex"].expire_all()
        self.assertEqual(
            self.sessions["globex"].get(OrderPaymentAttempt, self.globex_row.id).receipt_file_url,
            _key("globex", "g.jpg"),
        )

    def test_filas_que_no_cumplen_quedan_intactas_y_reportadas_sin_fallar(self):
        before = {n: self._value(n) for n in ("key", "null", "otro_origen", "fuera_de_convencion",
                                                "otra_carpeta", "de_otro_negocio")}
        code, out = self._run(write=True, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual({n: self._value(n) for n in before}, before)
        self.assertIn("otro origen", out)
        self.assertIn("fuera de convención", out)

    def test_archivo_inexistente_queda_intacto_y_reportado(self):
        mig.object_exists.return_value = False
        before = self._value("legacy")
        code, out = self._run(write=True, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual(self._value("legacy"), before)
        self.assertIn("archivo inexistente", out)

    def test_r2_no_responde_deja_la_fila_intacta_y_el_script_continua(self):
        mig.object_exists.side_effect = StorageUnavailable("x")
        before = self._value("legacy")
        code, out = self._run(write=True, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual(self._value("legacy"), before)
        self.assertIn("no verificable", out)

    def test_segunda_corrida_no_modifica_ninguna_fila(self):
        self._run(write=True, revert=False)
        snapshot = {name: self._value(name) for name in self.rows}
        code, out = self._run(write=True, revert=False)
        self.assertEqual(code, 0)
        self.assertEqual({name: self._value(name) for name in self.rows}, snapshot)
        self.assertIn("0 a reescribir", out)          # SC-009

    def test_tenant_acota_el_recorrido(self):
        self._run(write=True, revert=False, tenant="acme")
        self.assertEqual(self._value("legacy"), _key("acme", "a.jpg"))
        self.sessions["globex"].expire_all()
        self.assertTrue(
            self.sessions["globex"].get(OrderPaymentAttempt, self.globex_row.id).receipt_file_url.startswith("https://")
        )

    def test_tenant_inexistente_es_error_operativo(self):
        code, _ = self._run(write=True, revert=False, tenant="no-existe")
        self.assertEqual(code, 1)

    def test_nunca_llama_a_r2_para_borrar_ni_subir(self):
        client = mock.Mock()
        with mock.patch("app.core.storage.get_r2_client", return_value=client):
            self._run(write=True, revert=False)
        client.delete_object.assert_not_called()
        client.put_object.assert_not_called()

    def test_un_esquema_que_falla_no_detiene_a_los_demas(self):
        original = mig._process_tenant

        def flaky(schema, **kwargs):
            if schema == "acme":
                raise RuntimeError("boom")
            return original(schema, **kwargs)

        with mock.patch.object(mig, "_process_tenant", flaky):
            code, _ = self._run(write=True, revert=False)
        self.assertEqual(code, 1)
        self.sessions["globex"].expire_all()
        self.assertEqual(
            self.sessions["globex"].get(OrderPaymentAttempt, self.globex_row.id).receipt_file_url,
            _key("globex", "g.jpg"),
        )

    # ------------------------------------------------------------------ reversión
    def test_revert_reconstruye_la_url_publica_solo_para_keys_del_propio_esquema(self):
        self._run(write=True, revert=False)
        code, _ = self._run(write=True, revert=True)
        self.assertEqual(code, 0)
        self.assertEqual(self._value("legacy"), f"{LEGACY}/{_key('acme', 'a.jpg')}")
        self.assertEqual(self._value("key"), f"{LEGACY}/{_key('acme', 'c.jpg')}")
        # nunca toca una URL de otro origen
        self.assertEqual(self._value("otro_origen"), "https://cdn.otro.com/comprobante.jpg")

    def test_revert_sin_apply_es_simulacion(self):
        self._run(write=True, revert=False)
        before = {name: self._value(name) for name in self.rows}
        _, out = self._run(write=False, revert=True)
        self.assertEqual({name: self._value(name) for name in self.rows}, before)
        self.assertIn("SIMULACIÓN DE LA REVERSIÓN", out)

    def test_revert_no_toca_una_key_de_otra_carpeta_ni_de_otro_negocio(self):
        self.rows["ajena"] = _attempt(self.sessions["acme"], "globex/comprobantes/z.jpg")
        self.rows["producto"] = _attempt(self.sessions["acme"], "acme/products/z.jpg")
        self._run(write=True, revert=True)
        self.assertEqual(self._value("ajena"), "globex/comprobantes/z.jpg")
        self.assertEqual(self._value("producto"), "acme/products/z.jpg")


if __name__ == "__main__":
    unittest.main()
