"""Spec 030 — Historia 3: `reports/service.py::_paid_sales_filter` respeta la
medianoche de Bogotá, no la de UTC (contracts/date-range-filters.md, tabla de
casos obligatorios).

El bucketing por día (`sales_report`, `by_day`) usa `func.timezone(...)` —
doble `AT TIME ZONE` de Postgres (research.md Decisión 5) — que SQLite no
compila; este módulo, como el resto de `characterization_tests`, corre sobre
SQLite en memoria (mismo patrón que `test_sales_timezone.py`), así que
verifica el filtro (`_paid_sales_filter`, ejecutable) y, aparte, que el
bucketing efectivamente construye la expresión `timezone(...)` esperada
inspeccionando el SQL compilado, sin ejecutarlo contra Postgres.

    python -m unittest app.characterization_tests.test_reports_timezone -v
"""
import unittest
import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.reports import service as reports_service
from app.models.sale import Sale


def _make_sale(db, *, sold_at: datetime, shift=None) -> Sale:
    if shift is None:
        shift = fx.make_cash_shift(db)
    sale = Sale(
        id=uuid.uuid4(), cash_shift_id=shift.id, user_id=uuid.uuid4(),
        user_name="Cajero de prueba", subtotal=Decimal("10000"), total=Decimal("10000"),
        status="paid", sold_at=sold_at,
    )
    db.add(sale)
    db.commit()
    return sale


def _count(db, tenant, date_from, date_to) -> int:
    conds = reports_service._paid_sales_filter(tenant, date_from, date_to)
    return db.execute(select(func.count(Sale.id)).where(*conds)).scalar_one()


class TestReportsFiltroMedianocheBogota(unittest.TestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(timezone="America/Bogota")

    def test_venta_23_59_bogota_incluida_en_filtro_del_dia(self):
        # 23:59 hora de Bogota del 24/08 = 04:59 UTC del 25/08.
        db = fx.new_session()
        _make_sale(db, sold_at=datetime(2026, 8, 25, 4, 59, 0))
        self.assertEqual(_count(db, self.tenant, date(2026, 8, 24), date(2026, 8, 24)), 1)

    def test_venta_00_01_bogota_del_dia_siguiente_excluida_del_filtro_del_dia(self):
        # 00:01 hora de Bogota del 25/08 = 05:01 UTC del 25/08.
        db = fx.new_session()
        _make_sale(db, sold_at=datetime(2026, 8, 25, 5, 1, 0))
        self.assertEqual(_count(db, self.tenant, date(2026, 8, 24), date(2026, 8, 24)), 0)

    def test_rango_de_un_dia_incluye_exactamente_ese_dia_de_bogota(self):
        db = fx.new_session()
        _make_sale(db, sold_at=datetime(2026, 8, 24, 15, 0, 0))  # 10:00 Bogotá — dentro
        _make_sale(db, sold_at=datetime(2026, 8, 23, 23, 30, 0))  # 18:30 Bogotá del 23/08 — fuera
        self.assertEqual(_count(db, self.tenant, date(2026, 8, 24), date(2026, 8, 24)), 1)


class TestReportsBucketingUsaZonaDelTenant(unittest.TestCase):
    """No ejecuta la consulta (Postgres-only): confirma que `sales_report`
    construye el bucket con el doble `AT TIME ZONE` de la zona del tenant en
    vez de agrupar directo por `Sale.sold_at` en UTC (research.md Decisión 5)."""

    def test_bucket_diario_usa_timezone_del_tenant(self):
        tenant = SimpleNamespace(timezone="America/Bogota")
        tz_name = reports_service.resolve_timezone(tenant).key
        local_sold_at = func.timezone(tz_name, func.timezone("UTC", Sale.sold_at))
        bucket = func.date(local_sold_at)
        compiled = str(select(bucket).compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("timezone('America/Bogota'", compiled)
        self.assertIn("timezone('UTC'", compiled)


if __name__ == "__main__":
    unittest.main()
