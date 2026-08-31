"""Tests de la nueva funcionalidad — spec 063-promociones-por-variante, US6:
el **paso de datos** de la revisión Alembic `063a` (`migrate_promotions_data`),
ejercitado sobre SQLite con el esquema pre-refactor sembrado
(contracts/migracion.md §1, §3).

Decisión de negocio: A-61 / A-62 (registro-de-anomalias.md, 2026-08-31).
La verificación end-to-end contra PostgreSQL real vive en el script
`scratchpad/verify_063a.py` (quickstart.md Paso 1 / T008 / T051).

`migrate_promotions_data` usa Core con `sa.Table` tipados propios, así que este
test arma su propio esquema SQLite (no depende del ORM, que en el Incremento F ya
no define `promotion_targets`).

Ejecutar solo este módulo:

    python -m unittest app.characterization_tests.test_promotions_migration -v
"""
import importlib.util
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import unittest

import sqlalchemy as sa

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test")

_MIG = Path(__file__).resolve().parents[2] / (
    "alembic/versions/387ef3e638cd_063a_promociones_por_conjunto_aditivo.py"
)
_spec = importlib.util.spec_from_file_location("mig_063a", _MIG)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


class TestMigratePromotionsData(unittest.TestCase):
    def setUp(self):
        # Esquema pre-refactor mínimo: lo que `migrate_promotions_data` lee.
        md = sa.MetaData()
        self.promotions = sa.Table(
            "promotions", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(255)),
            sa.Column("type", sa.String(50)),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("status", sa.String(16)),
            sa.Column("min_qty", sa.Integer),
            sa.Column("closed_by_refactor_at", sa.DateTime),
        )
        self.promotion_targets = sa.Table(
            "promotion_targets", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", sa.Uuid(as_uuid=True)),
            sa.Column("product_id", sa.Uuid(as_uuid=True)),
            sa.Column("category_id", sa.Uuid(as_uuid=True)),
        )
        self.products = sa.Table(
            "products", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("category_id", sa.Uuid(as_uuid=True)),
        )
        self.product_variants = sa.Table(
            "product_variants", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("product_id", sa.Uuid(as_uuid=True)),
            sa.Column("price", sa.Numeric(12, 2)),
            sa.Column("active", sa.Boolean),
        )
        self.promotion_variants = sa.Table(
            "promotion_variants", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", sa.Uuid(as_uuid=True)),
            sa.Column("product_variant_id", sa.Uuid(as_uuid=True)),
        )
        self.engine = sa.create_engine("sqlite:///:memory:")
        self.conn = self.engine.connect()
        md.create_all(self.conn)

        self.cat = _uid()
        self.prod = _uid()
        self.conn.execute(sa.insert(self.products), {"id": self.prod, "category_id": self.cat})
        self.v1, self.v2, self.inactiva = _uid(), _uid(), _uid()
        self.conn.execute(sa.insert(self.product_variants), [
            {"id": self.v1, "product_id": self.prod, "price": Decimal("8000"), "active": True},
            {"id": self.v2, "product_id": self.prod, "price": Decimal("6000"), "active": True},
            {"id": self.inactiva, "product_id": self.prod, "price": Decimal("5000"), "active": False},
        ])
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _promo(self, type_, status="active", name="p", value="0", min_qty=1):
        pid = _uid()
        self.conn.execute(sa.insert(self.promotions), {
            "id": pid, "name": name, "type": type_, "value": Decimal(value),
            "status": status, "min_qty": min_qty, "closed_by_refactor_at": None,
        })
        return pid

    def _target(self, pid, product_id=None, category_id=None):
        self.conn.execute(sa.insert(self.promotion_targets), {
            "id": _uid(), "promotion_id": pid,
            "product_id": product_id, "category_id": category_id,
        })

    def _run(self):
        mig.migrate_promotions_data(self.conn, None)
        self.conn.commit()

    def _row(self, pid):
        return self.conn.execute(
            sa.select(self.promotions).where(self.promotions.c.id == pid)
        ).one()

    def _variants(self, pid):
        return set(self.conn.execute(
            sa.select(self.promotion_variants.c.product_variant_id)
            .where(self.promotion_variants.c.promotion_id == pid)
        ).scalars())

    # ---- CA1: percent de categoría -> conjunto foto fija (solo variantes activas) ----
    def test_ca1_percent_de_categoria_materializa_conjunto_foto_fija(self):
        pid = self._promo("percent", value="10", name="10% Granizados")
        self._target(pid, category_id=self.cat)
        self._run()

        r = self._row(pid)
        self.assertEqual(r.type, "percent")
        self.assertEqual(r.status, "active")
        self.assertIsNone(r.closed_by_refactor_at)
        self.assertEqual(self._variants(pid), {self.v1, self.v2})  # la inactiva no

    def test_percent_de_producto_y_percent_global(self):
        de_prod = self._promo("percent", name="de producto")
        self._target(de_prod, product_id=self.prod)
        glob = self._promo("percent", name="global")  # sin targets
        self._run()
        self.assertEqual(self._variants(de_prod), {self.v1, self.v2})
        self.assertEqual(self._variants(glob), {self.v1, self.v2})

    # ---- CA2: combo -> Finalizada con marca, type histórico sin cambio ----
    def test_ca2_combo_pasa_a_finalizada_con_marca(self):
        pid = self._promo("combo", name="1 Litro + 1 Cono $30.000", value="30000")
        self._run()
        r = self._row(pid)
        self.assertEqual(r.status, "finished")
        self.assertIsNotNone(r.closed_by_refactor_at)
        self.assertEqual(r.type, "combo")
        self.assertEqual(self._variants(pid), set())

    # ---- CA4: fixed -> Finalizada, NO se convierte ----
    def test_ca4_fixed_pasa_a_finalizada_no_se_convierte(self):
        pid = self._promo("fixed", name="$2.000 por línea", value="2000")
        self._run()
        r = self._row(pid)
        self.assertEqual(r.status, "finished")
        self.assertEqual(r.type, "fixed")
        self.assertEqual(r.value, Decimal("2000"))

    def test_ca3_qty_price_presentation_pasa_a_finalizada(self):
        pid = self._promo("qty_price_presentation", name="2x1")
        self._run()
        r = self._row(pid)
        self.assertEqual(r.status, "finished")
        self.assertIsNotNone(r.closed_by_refactor_at)

    def test_promocion_ya_finished_no_se_toca(self):
        pid = self._promo("combo", status="finished", name="vieja")
        self._run()
        r = self._row(pid)
        self.assertEqual(r.status, "finished")
        self.assertIsNone(r.closed_by_refactor_at)  # no la cerró el refactor

    def test_las_finalizadas_por_el_refactor_son_filtrables(self):
        combo = self._promo("combo", name="combo")
        percent = self._promo("percent", name="percent")
        self._run()
        cerradas = set(self.conn.execute(
            sa.select(self.promotions.c.id)
            .where(self.promotions.c.closed_by_refactor_at.is_not(None))
        ).scalars())
        self.assertEqual(cerradas, {combo})
        self.assertIsNone(self._row(percent).closed_by_refactor_at)


if __name__ == "__main__":
    unittest.main()
