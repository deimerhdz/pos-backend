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

# spec 063 (revisión 2026-09-01, US6/T041-T044): paso de datos de `063c`
# (partición Promoción/Regla) — mismo patrón de carga que `063a` arriba.
_MIG_C = Path(__file__).resolve().parents[2] / (
    "alembic/versions/3ad34a2b8146_063c_promociones_reglas_aditivo.py"
)
_spec_c = importlib.util.spec_from_file_location("mig_063c", _MIG_C)
mig_c = importlib.util.module_from_spec(_spec_c)
_spec_c.loader.exec_module(mig_c)


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


class TestMigratePromotionRulesData(unittest.TestCase):
    """spec 063 (revisión 2026-09-01), US6/T041-T044: el paso de datos de
    `063c` (`migrate_promotion_rules_data`) — una `PromotionRule` por cada
    `Promotion` existente, repuntando `promotion_variants`
    (contracts/migracion.md §1, data-model.md §"upgrade() — paso 5").
    """

    def setUp(self):
        md = sa.MetaData()
        self.promotions = sa.Table(
            "promotions", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("type", sa.String(50)),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("min_qty", sa.Integer),
            sa.Column("status", sa.String(16)),
        )
        self.promotion_rules = sa.Table(
            "promotion_rules", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", sa.Uuid(as_uuid=True)),
            sa.Column("type", sa.String(50)),
            sa.Column("value", sa.Numeric(12, 2)),
            sa.Column("min_qty", sa.Integer),
        )
        self.promotion_variants = sa.Table(
            "promotion_variants", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("promotion_id", sa.Uuid(as_uuid=True)),
            sa.Column("promotion_rule_id", sa.Uuid(as_uuid=True), nullable=True),
            sa.Column("product_variant_id", sa.Uuid(as_uuid=True)),
        )
        # T043: tabla ajena al paso de datos de 063c (nunca aparece en
        # `_data_tables`) — sirve para probar que la migración de
        # promociones/reglas no toca nada de ventas.
        self.sales = sa.Table(
            "sales", md,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("discount", sa.Numeric(12, 2)),
            sa.Column("total", sa.Numeric(12, 2)),
        )
        self.engine = sa.create_engine("sqlite:///:memory:")
        self.conn = self.engine.connect()
        md.create_all(self.conn)

    def tearDown(self):
        self.conn.close()

    def _promo(self, type_="percent", value="10", min_qty=1, status="active"):
        pid = _uid()
        self.conn.execute(sa.insert(self.promotions), {
            "id": pid, "type": type_, "value": Decimal(value),
            "min_qty": min_qty, "status": status,
        })
        return pid

    def _variant_row(self, promo_id):
        vid = _uid()
        self.conn.execute(sa.insert(self.promotion_variants), {
            "id": _uid(), "promotion_id": promo_id,
            "promotion_rule_id": None, "product_variant_id": vid,
        })
        return vid

    def _run(self):
        mig_c.migrate_promotion_rules_data(self.conn, None)
        self.conn.commit()

    def _rules_of(self, promo_id):
        return self.conn.execute(
            sa.select(self.promotion_rules)
            .where(self.promotion_rules.c.promotion_id == promo_id)
        ).all()

    def _variant_rule_ids(self, promo_id):
        return set(self.conn.execute(
            sa.select(self.promotion_variants.c.promotion_rule_id)
            .where(self.promotion_variants.c.promotion_id == promo_id)
        ).scalars())

    # ---- T041: percent existente -> exactamente una regla, mismo type/value/min_qty ----
    def test_percent_existente_termina_con_exactamente_una_regla(self):
        pid = self._promo(type_="percent", value="10", min_qty=1)
        self._variant_row(pid)
        self._variant_row(pid)
        self._run()

        rules = self._rules_of(pid)
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual(r.type, "percent")
        self.assertEqual(r.value, Decimal("10"))
        self.assertEqual(r.min_qty, 1)
        # promotion_variants repuntada a esa única regla — el conjunto no cambia.
        self.assertEqual(self._variant_rule_ids(pid), {r.id})

    def test_package_price_existente_termina_con_exactamente_una_regla(self):
        pid = self._promo(type_="package_price", value="12000", min_qty=2)
        self._variant_row(pid)
        self._run()
        rules = self._rules_of(pid)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].type, "package_price")
        self.assertEqual(rules[0].value, Decimal("12000"))
        self.assertEqual(rules[0].min_qty, 2)

    # ---- T042: Finalizada de tipo legado también gana su regla histórica,
    # sin que el paso de datos falle por ningún CHECK (hallazgo F1) ----
    def test_finalizada_de_tipo_legado_tambien_gana_su_regla_historica(self):
        for tipo_legado in ("combo", "fixed", "qty_price", "qty_price_presentation"):
            with self.subTest(tipo=tipo_legado):
                pid = self._promo(type_=tipo_legado, value="2000", min_qty=1, status="finished")
                self._run()
                rules = self._rules_of(pid)
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0].type, tipo_legado)

    def test_no_filtra_por_status_ninguna_promocion_queda_sin_regla(self):
        activa = self._promo(status="active")
        pausada = self._promo(status="paused")
        borrador = self._promo(status="draft")
        finalizada = self._promo(type_="combo", status="finished")
        self._run()
        for pid in (activa, pausada, borrador, finalizada):
            self.assertEqual(len(self._rules_of(pid)), 1)

    # ---- T043: Sale/Invoice/CustomerOrder (representadas aquí por `sales`)
    # no las toca el paso de datos de 063c — opera solo sobre promociones ----
    def test_no_toca_ninguna_tabla_de_ventas(self):
        sale_id = _uid()
        self.conn.execute(sa.insert(self.sales), {
            "id": sale_id, "discount": Decimal("1000.00"), "total": Decimal("9000.00"),
        })
        self.conn.commit()
        self._promo()
        self._run()

        row = self.conn.execute(
            sa.select(self.sales).where(self.sales.c.id == sale_id)
        ).one()
        self.assertEqual(row.discount, Decimal("1000.00"))
        self.assertEqual(row.total, Decimal("9000.00"))

    # ---- T044: migrar una promoción ya migrada por 063a (percent con
    # conjunto) produce el mismo resultado 1:1 que cualquier otra ----
    def test_promocion_ya_migrada_por_063a_produce_una_regla_equivalente(self):
        """`063a` deja cada `percent` con su conjunto ya resuelto en
        `promotion_variants` (foto fija, FR-026) — `063c` no vuelve a leer
        `targets`, solo copia `type`/`value`/`min_qty` de `promotions` y
        repunta las filas de `promotion_variants` que ya existían."""
        pid = self._promo(type_="percent", value="10", min_qty=1)
        v1 = self._variant_row(pid)
        v2 = self._variant_row(pid)
        self._run()

        rules = self._rules_of(pid)
        self.assertEqual(len(rules), 1)
        variant_ids = set(self.conn.execute(
            sa.select(self.promotion_variants.c.product_variant_id)
            .where(self.promotion_variants.c.promotion_id == pid)
        ).scalars())
        self.assertEqual(variant_ids, {v1, v2})


if __name__ == "__main__":
    unittest.main()
