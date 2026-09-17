"""Tests de la nueva funcionalidad — spec 083-presentaciones-y-promociones:
el **paso de datos** de la revisión Alembic `da7581f7bb18` (siembra inicial,
FR-019), ejercitado sobre SQLite en memoria (mismo patrón que
`test_promotions_migration.py`, `BackfillFormulaTests` de
`test_category_display_order.py`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_presentations_migration -v
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
    "alembic/versions/da7581f7bb18_083_presentaciones_aditivo.py"
)
_spec = importlib.util.spec_from_file_location("mig_083_presentaciones", _MIG)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


class SeedPresentationsDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine("sqlite:///:memory:")
        self.conn = self.engine.connect()
        self.conn.execute(sa.text(
            "CREATE TABLE product_variants (id TEXT PRIMARY KEY, name TEXT)"
        ))
        self.conn.execute(sa.text(
            "CREATE TABLE presentations "
            "(id TEXT PRIMARY KEY, name TEXT, active BOOLEAN, "
            "created_at DATETIME, updated_at DATETIME)"
        ))
        self.conn.execute(sa.text(
            "CREATE TABLE category_presentations "
            "(id TEXT PRIMARY KEY, category_id TEXT, presentation_id TEXT)"
        ))

    def tearDown(self):
        self.conn.close()

    def _seed_variants(self, names):
        for name in names:
            self.conn.execute(
                sa.text("INSERT INTO product_variants (id, name) VALUES (:id, :name)"),
                {"id": str(uuid4()), "name": name},
            )
        self.conn.commit()

    def _run(self):
        sql = mig.seed_presentations_sql("main").replace(
            '"main".presentations', "presentations"
        ).replace('"main".product_variants', "product_variants")
        # SQLite no trae gen_random_uuid()/now() (funciones de Postgres) -- se
        # sustituyen aquí, solo para el test, por equivalentes de SQLite.
        sql = sql.replace("gen_random_uuid()", "lower(hex(randomblob(16)))")
        sql = sql.replace("now()", "CURRENT_TIMESTAMP")
        self.conn.execute(sa.text(sql))
        self.conn.commit()

    def _presentations(self):
        return self.conn.execute(
            sa.text("SELECT name, active FROM presentations ORDER BY name")
        ).all()

    def test_una_presentacion_activa_por_cada_nombre_distinto(self):
        self._seed_variants(["Pequeño", "Mediano", "Grande", "Pequeño"])
        self._run()

        rows = self._presentations()
        self.assertEqual([r.name for r in rows], ["Grande", "Mediano", "Pequeño"])
        self.assertTrue(all(bool(r.active) for r in rows))

    def test_comparacion_exacta_de_texto_no_normaliza(self):
        """FR-019: "Single" y "single" son dos presentaciones distintas -- sin
        TRIM/LOWER, mismo criterio literal que `uq__product_variants__product_id__name`."""
        self._seed_variants(["Single", "single", " Single "])
        self._run()

        nombres = {r.name for r in self._presentations()}
        self.assertEqual(nombres, {"Single", "single", " Single "})

    def test_sin_variantes_no_siembra_nada(self):
        self._run()
        self.assertEqual(self._presentations(), [])

    def test_no_crea_ninguna_asociacion_categoria_presentacion(self):
        self._seed_variants(["Pequeño", "Grande"])
        self._run()

        count = self.conn.execute(
            sa.text("SELECT COUNT(*) FROM category_presentations")
        ).scalar()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
