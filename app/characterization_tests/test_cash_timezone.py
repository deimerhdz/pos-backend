"""Spec 030 — Historia 2: Caja (`ShiftResponse`, `CashMovementResponse`,
`PartialCountResponse`) usa el mismo mecanismo central (`UtcDatetime`) que
Ventas (A-50, registro-de-anomalias.md).

    python -m unittest app.characterization_tests.test_cash_timezone -v
"""
import unittest
import uuid
from datetime import datetime
from decimal import Decimal

from app.api.v1.cash import schemas as cash_schemas
from app.core.timezone import resolve_timezone


class TestShiftUtcSerialization(unittest.TestCase):
    def test_opened_at_y_closed_at_llevan_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = cash_schemas.ShiftResponse.model_construct(
            id=uuid.uuid4(), cash_register_id=uuid.uuid4(), user_id=uuid.uuid4(),
            opening_amount=Decimal("0"), opened_at=naive, closed_at=naive, status="closed",
        )
        dumped = resp.model_dump(mode="json")
        self.assertTrue(dumped["opened_at"].endswith("+00:00"), dumped["opened_at"])
        self.assertTrue(dumped["closed_at"].endswith("+00:00"), dumped["closed_at"])

    def test_cierre_23_59_bogota_se_ve_como_el_dia_de_bogota_correcto(self):
        # 23:59 hora de Bogota del 24/08 = 04:59 UTC del 25/08.
        naive_utc = datetime(2026, 8, 25, 4, 59, 0)
        resp = cash_schemas.ShiftResponse.model_construct(
            id=uuid.uuid4(), cash_register_id=uuid.uuid4(), user_id=uuid.uuid4(),
            opening_amount=Decimal("0"), opened_at=naive_utc, closed_at=naive_utc, status="closed",
        )
        dumped = resp.model_dump(mode="json")["closed_at"]
        local = datetime.fromisoformat(dumped).astimezone(resolve_timezone(None))
        self.assertEqual(local.date().isoformat(), "2026-08-24")
        self.assertEqual((local.hour, local.minute), (23, 59))


class TestCashMovementUtcSerialization(unittest.TestCase):
    def test_occurred_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = cash_schemas.CashMovementResponse.model_construct(
            id=uuid.uuid4(), cash_shift_id=uuid.uuid4(), kind="ingreso",
            amount=Decimal("5000"), occurred_at=naive,
        )
        dumped = resp.model_dump(mode="json")["occurred_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)


class TestPartialCountUtcSerialization(unittest.TestCase):
    def test_counted_at_lleva_offset_utc_explicito(self):
        naive = datetime(2026, 8, 24, 12, 53, 7)
        resp = cash_schemas.PartialCountResponse.model_construct(
            id=uuid.uuid4(), cash_shift_id=uuid.uuid4(),
            counted_amount=Decimal("10000"), expected_amount=Decimal("10000"),
            difference=Decimal("0"), counted_at=naive,
        )
        dumped = resp.model_dump(mode="json")["counted_at"]
        self.assertTrue(dumped.endswith("+00:00"), dumped)


if __name__ == "__main__":
    unittest.main()
