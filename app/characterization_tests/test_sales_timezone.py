"""Spec 030 — Historia 1 (defecto reportado) e Historia 6 (valor histórico
intacto): `sales/schemas.py:SaleResponse.sold_at` viaja con offset UTC
explícito y `sales/service.py:list_sales_query` filtra por medianoche de
Bogotá, no UTC — A-50 (registro-de-anomalias.md).

    python -m unittest app.characterization_tests.test_sales_timezone -v
"""
import unittest
import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.characterization_tests import orders_fixtures as fx
from app.api.v1.sales import schemas as sales_schemas
from app.api.v1.sales import service as sales_service
from app.models.sale import Sale


def _make_sale(db, *, sold_at: datetime, shift=None, **kw) -> Sale:
    if shift is None:
        shift = fx.make_cash_shift(db)
    kw.setdefault("id", uuid.uuid4())
    kw.setdefault("cash_shift_id", shift.id)
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("user_name", "Cajero de prueba")
    kw.setdefault("subtotal", Decimal("10000"))
    kw.setdefault("total", Decimal("10000"))
    kw.setdefault("status", "paid")
    sale = Sale(sold_at=sold_at, **kw)
    db.add(sale)
    db.commit()
    return sale


class TestSaleSoldAtUtcSerialization(unittest.TestCase):
    def test_sold_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = sales_schemas.SaleResponse.model_construct(
            id=uuid.uuid4(), cash_shift_id=uuid.uuid4(), user_id=uuid.uuid4(),
            subtotal=Decimal("10000"), discount=Decimal("0"), tax=Decimal("0"),
            tip=Decimal("0"), total=Decimal("10000"), status="paid", sold_at=naive,
        )
        dumped = resp.model_dump(mode="json")["sold_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)

    def test_payment_paid_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = sales_schemas.PaymentResponse.model_construct(
            id=uuid.uuid4(), payment_method_id=uuid.uuid4(), amount=Decimal("10000"),
            paid_at=naive,
        )
        dumped = resp.model_dump(mode="json")["paid_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)


class TestSalesFiltroMedianocheBogota(unittest.TestCase):
    """El filtro real vive en `sales/service.py::list_sales_query` (US3, Fase
    6) — este test documenta el contrato desde la Historia 1 (quickstart.md
    Paso 3)."""

    def test_venta_23_59_bogota_incluida_en_filtro_del_dia(self):
        # 23:59 hora de Bogota del 24/08 = 04:59 UTC del 25/08.
        db = fx.new_session()
        tenant = SimpleNamespace(timezone="America/Bogota")
        sale = _make_sale(db, sold_at=datetime(2026, 8, 25, 4, 59, 0))

        stmt = sales_service.list_sales_query(
            tenant=tenant, date_from=date(2026, 8, 24), date_to=date(2026, 8, 24),
        )
        ids = [s.id for s in db.execute(stmt).scalars().all()]
        self.assertIn(sale.id, ids)

    def test_venta_00_01_bogota_del_dia_siguiente_excluida(self):
        # 00:01 hora de Bogota del 25/08 = 05:01 UTC del 25/08.
        db = fx.new_session()
        tenant = SimpleNamespace(timezone="America/Bogota")
        sale = _make_sale(db, sold_at=datetime(2026, 8, 25, 5, 1, 0))

        stmt = sales_service.list_sales_query(
            tenant=tenant, date_from=date(2026, 8, 24), date_to=date(2026, 8, 24),
        )
        ids = [s.id for s in db.execute(stmt).scalars().all()]
        self.assertNotIn(sale.id, ids)


class TestValorAlmacenadoNoCambia(unittest.TestCase):
    """Historia 6 / FR-007 / FR-010: la corrección no recalcula ni altera el
    valor almacenado de una venta ya existente (quickstart.md Paso 6)."""

    def test_valor_almacenado_no_cambia(self):
        db = fx.new_session()
        tenant = SimpleNamespace(timezone="America/Bogota")
        sale = _make_sale(db, sold_at=datetime(2026, 8, 20, 15, 0, 0))
        antes = sale.sold_at

        # Ejercita la ruta de lectura/serialización introducida por esta spec.
        stmt = sales_service.list_sales_query(tenant=tenant)
        loaded = db.execute(stmt).scalars().all()
        sales_schemas.SaleResponse.model_validate(
            next(s for s in loaded if s.id == sale.id)
        )

        db.refresh(sale)
        self.assertEqual(sale.sold_at, antes)


if __name__ == "__main__":
    unittest.main()
