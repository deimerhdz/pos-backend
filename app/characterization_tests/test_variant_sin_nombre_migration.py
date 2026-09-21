"""Tests de la nueva funcionalidad — spec 084-fix-promociones-productos, enmienda 2026-09-20
(US7, A-79): el **paso de datos** de la revisión Alembic `c7e2b91a4d35` (nombres libres ->
filas del catálogo + enlace), ejercitado sobre SQLite en memoria (mismo patrón que
`test_presentations_migration.py`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_variant_sin_nombre_migration -v
"""
import importlib.util
import os
import unittest
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test")

_MIG = Path(__file__).resolve().parents[2] / (
    "alembic/versions/c7e2b91a4d35_084_variante_sin_nombre.py"
)
_spec = importlib.util.spec_from_file_location("mig_084_variante_sin_nombre", _MIG)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


class EnlazarNombresLibresTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite:///:memory:")
        self.conn = self.engine.connect()
        self.conn.execute(sa.text(
            "CREATE TABLE presentations "
            "(id TEXT PRIMARY KEY, name TEXT UNIQUE, active BOOLEAN, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        self.conn.execute(sa.text(
            "CREATE TABLE product_variants "
            "(id TEXT PRIMARY KEY, product_id TEXT, name TEXT, presentation_id TEXT)"
        ))

    def tearDown(self):
        self.conn.close()

    def _presentation(self, name, active=True):
        pid = str(uuid4())
        self.conn.execute(
            sa.text("INSERT INTO presentations (id, name, active) VALUES (:i, :n, :a)"),
            {"i": pid, "n": name, "a": active},
        )
        return pid

    def _variant(self, product_id, name, presentation_id=None):
        vid = str(uuid4())
        self.conn.execute(
            sa.text(
                "INSERT INTO product_variants (id, product_id, name, presentation_id) "
                "VALUES (:i, :p, :n, :pr)"
            ),
            {"i": vid, "p": product_id, "n": name, "pr": presentation_id},
        )
        return vid

    def _run(self):
        for fn in (mig.seed_free_names_sql, mig.link_free_names_sql):
            sql = fn("main").replace('"main".presentations', "presentations").replace(
                '"main".product_variants', "product_variants"
            )
            # SQLite no trae gen_random_uuid()/now() (funciones de Postgres).
            sql = sql.replace("gen_random_uuid()", "lower(hex(randomblob(16)))")
            sql = sql.replace("now()", "CURRENT_TIMESTAMP")
            self.conn.execute(sa.text(sql))
        self.conn.commit()

    def _rows(self):
        return self.conn.execute(sa.text(
            "SELECT v.id, v.name AS variant_name, p.name AS presentation_name, p.active "
            "FROM product_variants v LEFT JOIN presentations p ON p.id = v.presentation_id"
        )).all()

    def test_nombre_libre_crea_su_presentacion_y_enlaza(self):
        prod = str(uuid4())
        self._variant(prod, "Familiar")
        self._run()

        (row,) = self._rows()
        self.assertEqual(row.presentation_name, "Familiar")
        self.assertTrue(bool(row.active))

    def test_reutiliza_la_presentacion_existente_aunque_este_desactivada(self):
        self._presentation("Retirada", active=False)
        self._variant(str(uuid4()), "Retirada")
        self._run()

        (row,) = self._rows()
        self.assertEqual(row.presentation_name, "Retirada")
        self.assertFalse(bool(row.active))
        self.assertEqual(
            self.conn.execute(sa.text("SELECT COUNT(*) FROM presentations")).scalar(), 1
        )

    def test_el_mismo_nombre_libre_en_dos_productos_comparte_una_fila(self):
        self._variant(str(uuid4()), "Familiar")
        self._variant(str(uuid4()), "Familiar")
        self._run()

        rows = self._rows()
        self.assertEqual({r.presentation_name for r in rows}, {"Familiar"})
        self.assertEqual(
            self.conn.execute(sa.text("SELECT COUNT(*) FROM presentations")).scalar(), 1
        )

    def test_comparacion_exacta_no_normaliza_mayusculas(self):
        prod = str(uuid4())
        self._variant(prod, "Grande")
        self._variant(prod, "grande")
        self._run()

        rows = self._rows()
        self.assertEqual({r.presentation_name for r in rows}, {"Grande", "grande"})
        # Dos filas de catálogo distintas => el UNIQUE(product_id, presentation_id) no choca.
        self.assertEqual(len({(prod, r.id) for r in rows}), 2)

    def test_variantes_ya_enlazadas_no_se_tocan(self):
        pid = self._presentation("Grande")
        self._variant(str(uuid4()), "nombre viejo desalineado", presentation_id=pid)
        self._run()

        (row,) = self._rows()
        self.assertEqual(row.presentation_name, "Grande")
        self.assertEqual(
            self.conn.execute(sa.text("SELECT COUNT(*) FROM presentations")).scalar(), 1
        )

    def test_ninguna_variante_queda_sin_presentacion(self):
        for n in ("A", "B", "C", "A"):
            self._variant(str(uuid4()), n)
        self._run()

        self.assertEqual(
            self.conn.execute(sa.text(
                "SELECT COUNT(*) FROM product_variants WHERE presentation_id IS NULL"
            )).scalar(),
            0,
        )

    def test_es_idempotente(self):
        self._variant(str(uuid4()), "Familiar")
        self._run()
        self._run()

        self.assertEqual(
            self.conn.execute(sa.text("SELECT COUNT(*) FROM presentations")).scalar(), 1
        )


if __name__ == "__main__":
    unittest.main()
